"""Cross-check our lake water areas against independent Google Earth Engine data.

For each lake and date we compare our measurement (from the time-series CSV)
with three other sources, counted inside the same outline (+30 m):
  dw   Google Dynamic World (10 m land cover, same-day Sentinel-2 image)
  jrc  JRC Global Surface Water monthly history (30 m, until Dec 2021)
  s1   Sentinel-1 radar, VV below a dB threshold, within +-6 days (sees
       through cloud; rough, uses a fixed threshold)

Run (needs `earthengine authenticate` done, and EE_PROJECT set):
  export EE_PROJECT=aqua-1000
  python ai_agent/scripts/gee_crosscheck.py --top 12 --per-lake 8
  python ai_agent/scripts/gee_crosscheck.py --only "Varthur" --per-lake 12

Output: ai_agent/reports/gee/gee_crosscheck.csv and a printed summary.
This tool only COMPARES; none of the three is ground truth. Agreement is
evidence, disagreement is a reason to look at the pictures.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import date as Date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
SERIES = ROOT / "reports" / "lakes" / "lake_water_timeseries.csv"
OUT_DIR = ROOT / "reports" / "gee"
BUFFER_M = 30.0
FIELDS = ["lake_id", "lake", "date", "ours_ha", "dw_ha", "jrc_ha", "s1_ha"]


# ---------------------------------------------------------------- selection
def pick_rows(rows, top, per_lake, only=None, min_ha=5.0):
    """Archive rows with status ok; the `top` largest lakes; per_lake dates
    spread evenly over time."""
    by_lake = defaultdict(list)
    for r in rows:
        if (r["source"] == "archive" and r["status"] == "ok"
                and r["water_ha"] != ""
                and float(r["outline_ha"] or 0) >= min_ha):
            if only and not any(o.lower() in r["lake"].lower() for o in only):
                continue
            by_lake[r["lake_id"]].append(r)
    lakes = sorted(by_lake.values(),
                   key=lambda rs: -float(rs[0]["outline_ha"]))
    if not only:
        lakes = lakes[:top]
    chosen = []
    for rs in lakes:
        rs = sorted({r["date"]: r for r in rs}.values(), key=lambda r: r["date"])
        if len(rs) > per_lake:
            idx = np.linspace(0, len(rs) - 1, per_lake).round().astype(int)
            rs = [rs[i] for i in sorted(set(idx))]
        chosen.extend(rs)
    return chosen


# ----------------------------------------------------------------- summary
def summarise(records):
    """Per source: how many comparisons, median ours/other ratio, share within
    +-25 %, rank correlation."""
    from scipy.stats import spearmanr
    out = {}
    for key in ("dw_ha", "jrc_ha", "s1_ha"):
        pairs = [(float(r["ours_ha"]), float(r[key])) for r in records
                 if r.get(key) not in (None, "") and r.get("ours_ha") not in (None, "")]
        if not pairs:
            out[key] = None
            continue
        a = np.array(pairs)
        big = a[(a[:, 0] > 0.5) | (a[:, 1] > 0.5)]
        ratio = big[:, 0] / np.maximum(big[:, 1], 1e-6) if len(big) else np.array([])
        rho = (spearmanr(a[:, 0], a[:, 1])[0]
               if len(a) >= 5 and a[:, 0].std() > 0 and a[:, 1].std() > 0
               else float("nan"))
        out[key] = {
            "n": len(a),
            "median_ratio": float(np.median(ratio)) if len(ratio) else float("nan"),
            "within_25pct": (float(np.mean(np.abs(ratio - 1) <= 0.25))
                             if len(ratio) else float("nan")),
            "spearman": float(rho),
        }
    return out


def print_summary(records):
    names = {"dw_ha": "Dynamic World", "jrc_ha": "JRC monthly",
             "s1_ha": "Sentinel-1 radar"}
    print(f"\n{len(records)} lake-dates compared")
    for key, s in summarise(records).items():
        if s is None:
            print(f"  {names[key]:17s} no data")
            continue
        print(f"  {names[key]:17s} n={s['n']:3d}  median(ours/theirs)="
              f"{s['median_ratio']:.2f}  within +-25%: "
              f"{s['within_25pct'] * 100:.0f}%  rank correlation: "
              f"{s['spearman']:.2f}")
    print("\nratio near 1.0 = same area; correlation near 1 = same ups and "
          "downs over time. None of these is ground truth.")


def run(rows, fetch, out_csv=None):
    records = []
    for i, r in enumerate(rows, 1):
        got = fetch(r)
        rec = {"lake_id": r["lake_id"], "lake": r["lake"], "date": r["date"],
               "ours_ha": r["water_ha"]}
        rec.update({k: ("" if got.get(k) is None else round(got[k], 2))
                    for k in ("dw_ha", "jrc_ha", "s1_ha")})
        records.append(rec)
        print(f"[{i}/{len(rows)}] {r['lake'][:26]:26s} {r['date']}  ours "
              f"{r['water_ha']}  dw {rec['dw_ha']}  jrc {rec['jrc_ha']}  "
              f"s1 {rec['s1_ha']}", flush=True)
    if out_csv:
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(out_csv, "w", newline="", encoding="utf-8") as handle:
            w = csv.DictWriter(handle, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(records)
        print(f"\nsaved {out_csv}")
    return records


# --------------------------------------------------------- Earth Engine part
def make_fetch(project, s1_db):
    import ee
    from shapely.geometry import mapping
    import lakewater as lw
    ee.Initialize(project=project)
    lakes = {l["id"]: l for l in lw.load_lakes(min_ha=5.0)}
    cache = {}

    def region(lake_id):
        if lake_id not in cache:
            g = ee.Geometry(mapping(lakes[lake_id]["geom_ll"]), None, False)
            cache[lake_id] = g.buffer(BUFFER_M)
        return cache[lake_id]

    area_ha = ee.Image.pixelArea().divide(10000)

    def total(mask, geom, scale):
        return area_ha.updateMask(mask).reduceRegion(
            ee.Reducer.sum(), geom, scale, maxPixels=10_000_000, bestEffort=True)

    def fetch(r):
        geom = region(r["lake_id"])
        d0 = Date.fromisoformat(r["date"])
        d1 = d0 + timedelta(days=1)
        result = {}
        try:
            dw_col = (ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
                      .filterBounds(geom).filterDate(str(d0), str(d1)))
            n_dw = dw_col.size()
            dw = ee.Image(ee.Algorithms.If(
                n_dw.gt(0), dw_col.select("label").mosaic(),
                ee.Image.constant(-1).rename("label")))
            dw_area = total(dw.eq(0), geom, 10).get("area")
            # JRC monthly history, until 2021
            jrc_col = (ee.ImageCollection("JRC/GSW1_4/MonthlyHistory")
                       .filter(ee.Filter.calendarRange(d0.year, d0.year, "year"))
                       .filter(ee.Filter.calendarRange(d0.month, d0.month, "month")))
            jrc = jrc_col.first()
            jrc_band = ee.Image(ee.Algorithms.If(jrc, jrc, ee.Image(0))).select([0])
            jrc_water = total(jrc_band.eq(2), geom, 30).get("area")
            jrc_seen = total(jrc_band.gte(1), geom, 30).get("area")
            total_area = total(ee.Image(1), geom, 30).get("area")
            # Sentinel-1 radar, +-6 days, speckle-smoothed
            s1_col = (ee.ImageCollection("COPERNICUS/S1_GRD")
                      .filterBounds(geom)
                      .filterDate(str(d0 - timedelta(days=6)),
                                  str(d0 + timedelta(days=7)))
                      .filter(ee.Filter.eq("instrumentMode", "IW"))
                      .filter(ee.Filter.listContains(
                          "transmitterReceiverPolarisation", "VV"))
                      .select("VV"))
            n_s1 = s1_col.size()
            s1 = ee.Image(ee.Algorithms.If(
                n_s1.gt(0), s1_col.mean().focal_median(30, "circle", "meters"),
                ee.Image.constant(0).rename("VV")))
            s1_area = total(s1.lt(s1_db), geom, 10).get("area")
            info = ee.Dictionary({
                "n_dw": n_dw, "dw": dw_area, "jrc": jrc_water,
                "jrc_seen": jrc_seen, "total": total_area,
                "n_s1": n_s1, "s1": s1_area}).getInfo()
        except Exception as error:      # network / quota: keep going
            print(f"   Earth Engine error: {str(error)[:120]}")
            return {}
        if info.get("n_dw"):
            result["dw_ha"] = info.get("dw") or 0.0
        seen, tot = info.get("jrc_seen") or 0, info.get("total") or 0
        if tot and seen / tot >= 0.8:
            result["jrc_ha"] = info.get("jrc") or 0.0
        if info.get("n_s1"):
            result["s1_ha"] = info.get("s1") or 0.0
        return result

    return fetch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=12, help="largest N lakes")
    ap.add_argument("--per-lake", type=int, default=8)
    ap.add_argument("--only", nargs="*", help="lake name parts")
    ap.add_argument("--s1-db", type=float, default=-16.0,
                    help="radar VV below this dB counts as water")
    ap.add_argument("--project", default=os.environ.get("EE_PROJECT", ""))
    args = ap.parse_args()
    if not args.project:
        raise SystemExit("Set EE_PROJECT (export EE_PROJECT=aqua-1000)")
    with open(SERIES, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    chosen = pick_rows(rows, args.top, args.per_lake, args.only)
    print(f"{len(chosen)} lake-dates chosen")
    records = run(chosen, make_fetch(args.project, args.s1_db),
                  OUT_DIR / "gee_crosscheck.csv")
    print_summary(records)


if __name__ == "__main__":
    main()
