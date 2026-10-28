"""Buildings inside and around each lake, 2016-2023, from Google Open Buildings
2.5D Temporal (GOOGLE/Research/open-buildings-temporal/v1, Earth Engine).

Why: the land-cover U-Net works at 10 m and has one "built-up" class, so it
cannot see single buildings. Open Buildings Temporal gives, every year
2016-2023, a building-presence confidence and a building height at an
effective 4 m resolution (made from Sentinel-2 by a super-resolution model).
That is the closest free, repeatable source of building-level change.

For every featured lake and year it measures, in the same zones as the
land-cover rings (OpenStreetMap outline):
    inside (lakebed), 0-30 m (KTCDA buffer), 30-75 m, 75-200 m
  * building_ha        area where building_presence > THRESHOLD
  * mean_height_m      mean building height over that area
Sensitivity: the area is also computed at THRESHOLD +- 0.1, because the
presence values are uncalibrated confidences (Google's own note).

Output: ai_agent/reports/buildings/open_buildings_zones.csv

Run from the project root (Earth Engine account needed, ~1-2 min):
    python ai_agent/scripts/fetch_open_buildings.py --project aqua-1000
"""

import argparse
import csv
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAKES = ROOT / "data" / "lakes" / "bengaluru_lakes.geojson"
FEATURED = ROOT / "featured_lakes.csv"
OUT = ROOT / "reports" / "buildings" / "open_buildings_zones.csv"
COLLECTION = "GOOGLE/Research/open-buildings-temporal/v1"
YEARS = range(2016, 2024)
ZONES = [("inside", None, None), ("0-30 m", 0, 30), ("30-75 m", 30, 75), ("75-200 m", 75, 200)]
THRESHOLD = 0.5
SCALE_M = 4


def zone_features(ee, lakes):
    feats = []
    for lake in lakes:
        geom = ee.Geometry(lake["geometry"])
        for name, lo, hi in ZONES:
            if name == "inside":
                shape = geom
            else:
                inner = geom.buffer(lo, 1) if lo else geom
                shape = geom.buffer(hi, 1).difference(inner, 1)
            feats.append(ee.Feature(shape, {"lake_id": lake["properties"]["id"],
                                            "name": lake["properties"]["name"], "zone": name}))
    return ee.FeatureCollection(feats)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=os.environ.get("EE_PROJECT", ""))
    parser.add_argument("--all-lakes", action="store_true", help="every lake, not only the featured ones")
    args = parser.parse_args()
    if not args.project:
        raise SystemExit("Needs --project (Earth Engine project id) or EE_PROJECT.")
    import ee
    ee.Initialize(project=args.project)

    lakes = json.loads(LAKES.read_text())["features"]
    if not args.all_lakes:
        with open(FEATURED, encoding="utf-8") as handle:
            wanted = {r["lake_id"] for r in csv.DictReader(handle)}
        lakes = [f for f in lakes if f["properties"]["id"] in wanted]
    zones = zone_features(ee, lakes)
    area = ee.Image.pixelArea()

    rows = []
    for year in YEARS:
        img = (ee.ImageCollection(COLLECTION)
               .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
               .filterBounds(zones.geometry()).mosaic())
        presence, height = img.select("building_presence"), img.select("building_height")
        stack = ee.Image.cat([
            area.rename("zone_m2"),
            area.updateMask(presence.gt(THRESHOLD)).rename("b"),
            area.updateMask(presence.gt(THRESHOLD - 0.1)).rename("b_lo"),
            area.updateMask(presence.gt(THRESHOLD + 0.1)).rename("b_hi"),
            height.multiply(area).updateMask(presence.gt(THRESHOLD)).rename("h_area"),
        ])
        res = stack.reduceRegions(collection=zones, reducer=ee.Reducer.sum(), scale=SCALE_M).getInfo()
        for f in res["features"]:
            p = f["properties"]
            b = p.get("b") or 0.0
            rows.append({"lake_id": p["lake_id"], "name": p["name"], "zone": p["zone"], "year": year,
                         "zone_ha": round(p["zone_m2"] / 1e4, 3),
                         "building_ha": round(b / 1e4, 3),
                         "building_ha_t04": round((p.get("b_lo") or 0) / 1e4, 3),
                         "building_ha_t06": round((p.get("b_hi") or 0) / 1e4, 3),
                         "mean_height_m": round(p["h_area"] / b, 2) if b and p.get("h_area") else ""})
        print(f"{year}: {len(res['features'])} zones", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
