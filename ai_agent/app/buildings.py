"""Building footprints inside and around each lake, 2016-2023.

Source: Google Open Buildings 2.5D Temporal (Earth Engine), fetched by
scripts/fetch_open_buildings.py into reports/buildings/open_buildings_zones.csv.
A pixel counts as building where the model's presence confidence > 0.5
(effective 4 m resolution); the area is also given at 0.4 and 0.6 because the
confidences are uncalibrated.

This complements the U-Net "built-up" class: built-up also includes roads,
paving and walkways and is mapped at 10 m, so it moves more than the
building footprints themselves.
"""

import csv
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parents[1] / "reports" / "buildings" / "open_buildings_zones.csv"
ZONES = ["inside", "0-30 m", "30-75 m", "75-200 m"]
SOURCE = ("Google Open Buildings 2.5D Temporal v1 (Sentinel-2 based, effective 4 m), "
          "building presence > 0.5")


def _stamp():
    return CSV_PATH.stat().st_mtime if CSV_PATH.exists() else None


@lru_cache(maxsize=2)
def _rows(stamp):
    by_lake = defaultdict(list)
    if stamp is None:
        return by_lake
    with open(CSV_PATH, encoding="utf-8") as handle:
        for r in csv.DictReader(handle):
            for k in ("zone_ha", "building_ha", "building_ha_t04", "building_ha_t06"):
                r[k] = float(r[k])
            r["year"] = int(r["year"])
            r["mean_height_m"] = float(r["mean_height_m"]) if r["mean_height_m"] else None
            by_lake[r["lake_id"]].append(r)
    return by_lake


def _avg(rows, years, key):
    vals = [r[key] for r in rows if r["year"] in years]
    return sum(vals) / len(vals) if vals else None


def lake_buildings(lake_id):
    rows = _rows(_stamp()).get(lake_id)
    if not rows:
        return {"available": False,
                "note": "No building data. Run scripts/fetch_open_buildings.py --project <earth-engine-project>."}
    years = sorted({r["year"] for r in rows})
    first, last = years[:2], years[-2:]
    zones = {}
    for z in ZONES:
        zr = [r for r in rows if r["zone"] == z]
        by_year = {r["year"]: r for r in zr}
        change = {}
        for key in ("building_ha", "building_ha_t04", "building_ha_t06"):
            a, b = _avg(zr, first, key), _avg(zr, last, key)
            change[key] = round(b - a, 2)
        zone_ha = zr[-1]["zone_ha"]
        zones[z] = {
            "zone_ha": round(zone_ha, 2),
            "by_year": [{"year": y, "building_ha": by_year[y]["building_ha"],
                         "share_pct": round(100 * by_year[y]["building_ha"] / zone_ha, 2) if zone_ha else None,
                         "mean_height_m": by_year[y]["mean_height_m"]} for y in years],
            "change_ha": change["building_ha"],
            "change_range_ha": [min(change["building_ha_t06"], change["building_ha_t04"]),
                                max(change["building_ha_t06"], change["building_ha_t04"])],
        }
    return {"available": True, "source": SOURCE, "years": years,
            "compare": [f"{first[0]}-{first[-1]}", f"{last[0]}-{last[-1]}"], "zones": zones,
            "note": ("Building footprints only (no roads or paving). Change compares the averages of the first "
                     "and last two years; the range uses confidence thresholds 0.4 and 0.6.")}


def city_summary():
    data = _rows(_stamp())
    if not data:
        return None
    totals = {z: 0.0 for z in ZONES}
    per_lake = []
    for lake_id in data:
        b = lake_buildings(lake_id)
        for z in ZONES:
            totals[z] += b["zones"][z]["change_ha"]
        per_lake.append({"lake_id": lake_id, "name": data[lake_id][0]["name"],
                         "buffer_0_75_change_ha": round(b["zones"]["0-30 m"]["change_ha"]
                                                        + b["zones"]["30-75 m"]["change_ha"], 2),
                         "ring_75_200_change_ha": b["zones"]["75-200 m"]["change_ha"]})
    per_lake.sort(key=lambda r: -r["buffer_0_75_change_ha"])
    return {"lakes": len(per_lake), "change_ha_by_zone": {z: round(v, 2) for z, v in totals.items()},
            "per_lake": per_lake, "source": SOURCE}
