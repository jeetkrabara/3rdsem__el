"""Rain-inconsistent change events: when a lake gains or loses water in a way
the rain that fell cannot explain, something else happened - draining, a
sluice opened, desilting, de-weeding, sewage or treated-water inflow.

Why this design: for Bengaluru's managed, sewage-fed tanks the open-water level
over years is set mostly by management, so "predict the level from rainfall"
fails (yearly rain vs post-monsoon water is significant for only 2 of 19
lakes). But over a few weeks rain clearly drives the change. So we model the
CHANGE between consecutive clear images and flag the changes rain cannot explain.

Per lake:
  1. Intervals between consecutive clear measurements, <= 90 days apart.
  2. Rain-response model (robust Huber regression) of the change in open-water
     fraction  df = f1 - f0  on
        rain in the interval x (1 - f0)       (room left to fill)
        rain in the 30 days before x (1 - f0) (wet ground runs off more)
        dry-season days x f0, wet-season days x f0   (losses scale with water)
     Huber down-weights the unusual intervals, so the model learns the lake's
     typical rain response, not its management events.
  3. Leave-one-year-out residuals r (observed - rain-explained change). Their
     robust scale sigma = 1.4826 x MAD is not inflated by the events themselves.
  4. EPISODES: runs of consecutive intervals whose residuals share a sign are
     summed (CUSUM-style), so gradual draining over several images is caught.
     Within each run the stretch with the largest |sum r| / (sigma sqrt(k)) is
     tested; it is an EVENT if that score >= 3. sigma is never below the
     repeat-measurement noise (max 3 ha, 3% of outline). Only intervals between
     two cloud-masked 10 m archive images can form events (the 2015-16 local
     scenes have no cloud mask and are too noisy), and neither image may have
     thin cirrus over the lake (Sen2Cor class 10, "soft" in threshold_method):
     haze breaks the water up and fakes a loss. All intervals train the model;
     candidates rejected only for cirrus are counted, not hidden.
     The number of events per lake is set by the data, not by a percentile.
  4b. DETECTION POWER: synthetic drains of 5-160 ha are injected into every
     eligible interval of the lake's real series; the smallest size detected in
     >= 80% of positions is the lake's minimum detectable change.
  5. Each event is classified with the green-growth change inside the outline:
        loss + green growth up by >=70% of it -> open water covered by plants
        loss otherwise                        -> water removed (drained/diverted)
        gain + green growth down by >=70%     -> plants cleared (de-weeding)
        gain otherwise                        -> inflow not explained by rain
     plus lakes within 8 km with a same-direction event at the same time
     (a cascade or a city-wide operation).
  6. validation_events.csv lists documented events (news / official records)
     and is scored against the detections.

Limits: an event says "rain does not explain this", not who did it; the image
pair shows what changed. Cloud-free images are irregular, so a 60-90 day
interval can hide when exactly it happened. Mixed local (17-20 m) and archive
(10 m) images add some noise.
"""

import csv
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import lake_analytics as la
from . import rainfall

ROOT = Path(__file__).resolve().parents[1]
VALIDATION_CSV = ROOT / "validation_events.csv"
MAX_GAP_DAYS = 90
MIN_INTERVALS = 20
Z_EVENT = 3.0
SIGMA_FLOOR_HA, SIGMA_FLOOR_FRAC = 3.0, 0.03   # repeat-measurement noise is never below this
INJECT_HA = (5, 10, 20, 40, 80, 160)            # synthetic drains for the detection-power test
POWER = 0.8
WEED_SHARE = 0.7
NEIGHBOUR_KM = 8.0
DRY_MONTHS = {12, 1, 2, 3, 4, 5}
FEATURES = ["rain in interval x room to fill", "rain 30 d before x room to fill",
            "dry-season days x water", "wet-season days x water"]
TYPES = {
    "water_removed": "Water removed (drained, sluice opened, diverted) - rain does not explain the loss",
    "plants_covered_water": "Open water covered by plants (weeds / marsh) - not necessarily water lost",
    "plants_cleared": "Plants cleared (de-weeding) exposed water",
    "inflow_without_rain": "Water added that rain does not explain (sewage / treated water / diversion in)",
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _intervals(lake):
    rows = [r for r in la.lake_rows(lake["id"]) if r["status"] == "ok"]
    calendar, rain = rainfall.lake_rain(lake)
    index = {d: i for i, d in enumerate(calendar)}
    last_rain = calendar[int(np.where(np.isfinite(rain))[0][-1])]
    out = []
    for a, b in zip(rows, rows[1:]):
        d0, d1 = a["_date"], b["_date"]
        gap = (d1 - d0).days
        if gap <= 0 or gap > MAX_GAP_DAYS or d0 not in index or d1 > last_rain:
            continue
        if d0 - timedelta(days=30) < calendar[0]:
            continue
        i0, i1 = index[d0], index[d1]
        outline = _num(b.get("outline_ha")) or lake["area_ha"]
        f0, f1 = a["_water"] / outline, b["_water"] / outline
        days = [d0 + timedelta(days=k) for k in range(gap)]
        dry = sum(1 for d in days if d.month in DRY_MONTHS)
        out.append({
            "a": a, "b": b, "gap": gap, "outline": outline, "f0": f0, "df": f1 - f0,
            "rain": float(np.nansum(rain[i0:i1])), "rain_before": float(np.nansum(rain[i0 - 30:i0])),
            "dry_days": dry, "wet_days": gap - dry,
            "veg_change": (_num(b.get("vegetation_ha")) or 0) - (_num(a.get("vegetation_ha")) or 0),
            "mixed_sources": a["source"] != b["source"],
            "cirrus": "soft" in (a.get("threshold_method") or "") or "soft" in (b.get("threshold_method") or ""),
        })
    return out


def _X(iv):
    room = 1 - min(max(iv["f0"], 0.0), 1.0)
    return [iv["rain"] * room, iv["rain_before"] * room,
            iv["dry_days"] * iv["f0"], iv["wet_days"] * iv["f0"]]


def _fit(X, y):
    from sklearn.linear_model import HuberRegressor
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return make_pipeline(StandardScaler(), HuberRegressor(epsilon=1.35, max_iter=500)).fit(X, y)


def _climate_normal(calendar_rain, d0, d1):
    """Median rain over the same calendar window in other years."""
    calendar, rain = calendar_rain
    index = {d: i for i, d in enumerate(calendar)}
    vals = []
    for yr in range(calendar[0].year, calendar[-1].year + 1):
        try:
            s = d0.replace(year=yr)
            e = d1.replace(year=yr + (d1.year - d0.year))
        except ValueError:
            continue
        if s in index and e in index and yr != d0.year:
            vals.append(float(np.nansum(rain[index[s]:index[e]])))
    return float(np.median(vals)) if vals else None


@lru_cache(maxsize=64)
def _lake_events_cached(lake_id, stamp):
    lake = la._lake_by_id(lake_id)
    ivs = _intervals(lake)
    if len(ivs) < MIN_INTERVALS:
        return {"lake_id": lake_id, "name": lake["name"], "available": False,
                "reason": f"{len(ivs)} usable intervals; need {MIN_INTERVALS}."}
    X = np.array([_X(iv) for iv in ivs])
    y = np.array([iv["df"] for iv in ivs])
    years = np.array([iv["a"]["_date"].year for iv in ivs])
    model = _fit(X, y)
    oof = np.full(len(y), np.nan)
    for yr in np.unique(years):
        test = years == yr
        if (~test).sum() >= 10:
            oof[test] = _fit(X[~test], y[~test]).predict(X[test])
    ok = np.isfinite(oof)
    resid = y - np.where(ok, oof, model.predict(X))
    outline = ivs[-1]["outline"]
    floor = max(SIGMA_FLOOR_HA, SIGMA_FLOOR_FRAC * outline) / outline
    rr = resid[ok]
    sigma = max(1.4826 * float(np.median(np.abs(rr - np.median(rr)))), floor)
    archive = np.array([iv["a"]["source"] == "archive" and iv["b"]["source"] == "archive" for iv in ivs])
    clear = np.array([not iv["cirrus"] for iv in ivs])
    eligible = archive & clear
    # how many events thin cirrus would have produced (reported, not hidden)
    hazy = [best for best, _ in _episodes(ivs, resid, archive, sigma)
            if any(ivs[i]["cirrus"] for i in best)]
    scaler, huber = model.named_steps["standardscaler"], model.named_steps["huberregressor"]
    coefs = huber.coef_ / scaler.scale_
    ss = ((y[ok] - y[ok].mean()) ** 2).sum()
    skill = float(1 - (rr ** 2).sum() / ss) if ss else None
    expected = y - resid

    all_intervals = []
    for iv, r, e in zip(ivs, resid, expected):
        o = iv["outline"]
        all_intervals.append({
            "start": iv["a"]["_date"].isoformat(), "end": iv["b"]["_date"].isoformat(), "days": iv["gap"],
            "water_start_ha": round(iv["a"]["_water"], 2), "water_end_ha": round(iv["b"]["_water"], 2),
            "change_ha": round(iv["df"] * o, 2), "expected_change_ha": round(float(e) * o, 2),
            "unexplained_ha": round(float(r) * o, 2), "rain_mm": round(iv["rain"], 1),
            "score": round(float(r) / sigma, 2)})

    cal_rain = rainfall.lake_rain(lake)
    events = []
    for best, best_score in _episodes(ivs, resid, eligible, sigma):
        s = float(resid[best].sum())
        first, last = ivs[best[0]], ivs[best[-1]]
        o = last["outline"]
        change = (last["b"]["_water"] - first["a"]["_water"])
        rain_mm = sum(ivs[i]["rain"] for i in best)
        veg = sum(ivs[i]["veg_change"] for i in best)
        loss = s < 0
        moved = abs(change)
        if loss:
            kind = "plants_covered_water" if veg >= WEED_SHARE * moved else "water_removed"
        else:
            kind = "plants_cleared" if -veg >= WEED_SHARE * moved else "inflow_without_rain"
        d0, d1 = first["a"]["_date"], last["b"]["_date"]
        normal = _climate_normal(cal_rain, d0, d1)
        explained = float(expected[best].sum()) * o
        contributions = {name: round(float(sum(c * X[i][k] for i in best)) * o, 2)
                         for k, (name, c) in enumerate(zip(FEATURES, coefs))}
        events.append({
            "start": d0.isoformat(), "end": d1.isoformat(), "days": (d1 - d0).days,
            "intervals": len(best), "type": kind, "label": TYPES[kind],
            "direction": "loss" if loss else "gain",
            "water_start_ha": round(first["a"]["_water"], 2), "water_end_ha": round(last["b"]["_water"], 2),
            "change_ha": round(change, 2), "expected_change_ha": round(explained, 2),
            "unexplained_ha": round(s * o, 2), "rain_mm": round(rain_mm, 1),
            "rain_normal_mm": None if normal is None else round(normal, 1),
            "strength": round(best_score, 2), "green_growth_change_ha": round(veg, 2),
            "mixed_sources": any(ivs[i]["mixed_sources"] for i in best),
            "before": {"date": d0.isoformat(), "item_id": first["a"]["item_id"], "source": first["a"]["source"]},
            "after": {"date": d1.isoformat(), "item_id": last["b"]["item_id"], "source": last["b"]["source"]},
            "rain_explains_ha": contributions,
            "explanation": (
                f"Between {d0} and {d1} ({len(best)} image interval{'s' if len(best) > 1 else ''}) open water "
                f"went from {first['a']['_water']:.1f} to {last['b']['_water']:.1f} ha ({change:+.1f} ha) with "
                f"{rain_mm:.0f} mm of rain" + (f" (normal for those dates: {normal:.0f} mm)" if normal is not None else "")
                + f". This lake's usual response to that rain is {explained:+.1f} ha, so {s * o:+.1f} ha "
                f"({best_score:.1f} sigma) is not explained by rain"
                + (f"; green growth inside the outline changed {veg:+.1f} ha." if veg else ".")),
        })
    power = _detection_power(ivs, resid, eligible, sigma, outline)
    return {
        "lake_id": lake_id, "name": lake["name"], "available": True, "outline_ha": round(outline, 1),
        "detection_power": power,
        "rain_source": rainfall.rain_source(),
        "model": {"type": "Huber regression of the change in open-water fraction on rain x room to fill, "
                         "antecedent rain, and seasonal losses",
                  "intervals": len(ivs), "cv_r2": None if skill is None else round(skill, 3),
                  "sigma_ha": round(sigma * outline, 2), "z_event": Z_EVENT,
                  "suppressed_thin_cirrus": len(hazy),
                  "sigma_floor_ha": round(floor * outline, 2),
                  "rain_response_ha_per_100mm_empty": round(float(coefs[0]) * 100 * outline, 2)},
        "events": sorted(events, key=lambda ev: ev["start"], reverse=True),
        "intervals": all_intervals,
    }


def _episodes(ivs, resid, eligible, sigma):
    """[(interval indices, score)] for runs of touching, eligible, same-sign
    residuals whose strongest stretch scores >= Z_EVENT (|sum| / sigma sqrt k)."""
    runs, cur = [], []
    for i in range(len(ivs)):
        if not eligible[i]:
            if cur:
                runs.append(cur)
            cur = []
            continue
        if cur and ivs[i]["a"]["_date"] == ivs[cur[-1]]["b"]["_date"] \
                and np.sign(resid[i]) == np.sign(resid[cur[-1]]):
            cur.append(i)
        else:
            if cur:
                runs.append(cur)
            cur = [i]
    if cur:
        runs.append(cur)
    found = []
    for run in runs:
        best, best_score = None, 0.0
        for a_i in range(len(run)):                       # strongest sub-stretch of the run
            for b_i in range(a_i, len(run)):
                part = run[a_i:b_i + 1]
                score = abs(float(resid[part].sum())) / (sigma * np.sqrt(len(part)))
                if score > best_score:
                    best, best_score = part, score
        if best_score >= Z_EVENT:
            found.append((best, best_score))
    return found


def _detection_power(ivs, resid, eligible, sigma, outline):
    """Inject a drain of each size into every eligible interval in turn and see
    whether an event covering it is found. Returns recall per size and the
    minimum detectable change (smallest size with recall >= POWER)."""
    idx = np.where(eligible)[0]
    if len(idx) == 0:
        return None
    recall = {}
    for size in INJECT_HA:
        hits = 0
        for i in idx:
            r = resid.copy()
            r[i] -= size / outline
            hits += any(i in part and r[part].sum() < 0 for part, _ in _episodes(ivs, r, eligible, sigma))
        recall[size] = round(hits / len(idx), 2)
    mdc = next((s for s in INJECT_HA if recall[s] >= POWER), None)
    return {"recall_by_size_ha": recall, "min_detectable_ha": mdc,
            "min_detectable_pct_of_outline": None if mdc is None else round(100 * mdc / outline, 1),
            "positions_tested": int(len(idx)),
            "note": f"Synthetic drains injected into each of {len(idx)} archive intervals; "
                    f"minimum detectable = smallest size found in >= {POWER:.0%} of positions."}


def _stamp():
    s = la._stamp()
    return (s, rainfall.RAIN_CSV.stat().st_mtime if rainfall.RAIN_CSV.exists() else None)


def lake_events(lake):
    out = _lake_events_cached(lake["id"], _stamp())
    if out.get("available"):
        _add_co_events(lake, out)
    return out


def _overlap(e1, e2):
    return e1["start"] <= e2["end"] and e2["start"] <= e1["end"]


def _add_co_events(lake, out):
    here = lake["geom"].representative_point()
    for other in la._load_lakes():
        if other["id"] == lake["id"]:
            continue
        p = other["geom"].representative_point()
        if here.distance(p) * 111.32 > NEIGHBOUR_KM:
            continue
        res = _lake_events_cached(other["id"], _stamp()) if la.lake_rows(other["id"]) else None
        if not res or not res.get("available"):
            continue
        for ev in out["events"]:
            same = [o for o in res["events"] if o["direction"] == ev["direction"] and _overlap(ev, o)]
            if same and other["id"] not in {x["lake_id"] for x in ev.get("also_at", [])}:
                ev.setdefault("also_at", []).append(
                    {"lake_id": other["id"], "name": other["name"],
                     "start": same[0]["start"], "end": same[0]["end"], "change_ha": same[0]["change_ha"]})


def validation():
    """Score detections against documented events."""
    if not VALIDATION_CSV.exists():
        return {"cases": [], "note": "No validation_events.csv."}
    cases = []
    with open(VALIDATION_CSV, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            lake = la._lake_by_id(row["lake_id"])
            res = lake_events(lake) if lake else None
            window = {"start": row["start"], "end": row["end"]}
            hits = [e for e in (res or {}).get("events", [])
                    if _overlap(e, window) and e["direction"] == row["expected"]]
            covered = [i for i in (res or {}).get("intervals", []) if _overlap(i, window)]
            cases.append({**{k: row[k] for k in ("lake_id", "lake", "start", "end", "expected",
                                                 "what_happened", "source")},
                          "detected": bool(hits), "observed": bool(covered),
                          "detections": [{k: e[k] for k in ("start", "end", "type", "change_ha",
                                                            "unexplained_ha")} for e in hits],
                          "intervals_in_window": covered})
    observed = [c for c in cases if c["observed"]]
    return {"cases": cases, "documented": len(cases), "with_clear_images": len(observed),
            "detected": sum(c["detected"] for c in observed),
            "note": "A documented event can only be detected if clear images exist before and after it."}


def city_events(limit=None):
    items, skipped, lakes = [], [], []
    for lake in la._load_lakes():
        if not la.lake_rows(lake["id"]):
            continue
        res = lake_events(lake)
        if not res.get("available"):
            skipped.append({"lake_id": lake["id"], "name": lake["name"], "reason": res.get("reason")})
            continue
        power = res["detection_power"] or {}
        lakes.append({"lake_id": lake["id"], "name": lake["name"], "outline_ha": res["outline_ha"],
                      "intervals": res["model"]["intervals"], "sigma_ha": res["model"]["sigma_ha"],
                      "events": len(res["events"]), "min_detectable_ha": power.get("min_detectable_ha"),
                      "min_detectable_pct_of_outline": power.get("min_detectable_pct_of_outline")})
        for ev in res["events"]:
            items.append({"lake_id": lake["id"], "name": lake["name"],
                          **{k: ev[k] for k in ("start", "end", "type", "label", "direction", "change_ha",
                                                "unexplained_ha", "rain_mm", "strength")},
                          "also_at": [o["name"] for o in ev.get("also_at", [])]})
    items.sort(key=lambda e: e["end"], reverse=True)
    return {"events": items[:limit] if limit else items, "total": len(items), "skipped": skipped,
            "lakes": sorted(lakes, key=lambda l: -l["outline_ha"]),
            "validation": validation(), "rain_source": rainfall.rain_source()}
