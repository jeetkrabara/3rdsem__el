"""Re-measure archive images whose reflectance offset was applied by mistake.

Some 2022 archive images are labelled "processing baseline 04.00, offset not
yet removed" but their numbers carry no offset. decide_offset() used to
subtract 1000 anyway, which darkens everything by 10% reflectance: the
picture turns black and MNDWI (so the water area) is wrong. decide_offset()
now also checks the green band; this script finds the affected images in
lake_water_timeseries.csv and measures their rows again.

Run from the project root:
    python ai_agent/scripts/repair_offset_rows.py --dry-run   # list + compare
    python ai_agent/scripts/repair_offset_rows.py             # rewrite rows
A backup of the CSV is written next to it before anything is changed.
"""

import argparse
import csv
import shutil
import time
from pathlib import Path

import httpx
import rasterio

from lakewater import decide_offset, load_archive_scene, load_lakes, measure_lake

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "reports" / "lakes" / "lake_water_timeseries.csv"
ITEM_URL = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/{}"
FIRST_OFFSET_DATE = "2022-01-25"      # baseline 04.00 started here
GDAL = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
            GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="3", VSI_CACHE="TRUE")


def suspect(item):
    """True if the metadata says +1000 but the green band shows no offset."""
    props = item["properties"]
    stated = 1000.0 if float(props.get("s2:processing_baseline", 0)) >= 4.0 else 0.0
    if props.get("earthsearch:boa_offset_applied") is True or stated == 0.0:
        return False
    with rasterio.Env(**GDAL), rasterio.open(item["assets"]["green"]["href"]) as src:
        green = src.read(1, window=rasterio.windows.Window(3000, 3000, 600, 600))
    offset, _ = decide_offset(props, green, green > 0, green)
    return offset == 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with open(CSV_PATH, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    items = sorted({r["item_id"] for r in rows
                    if r["source"] == "archive" and r["date"] >= FIRST_OFFSET_DATE})
    print(f"{len(items)} archive images since {FIRST_OFFSET_DATE}; checking metadata...")

    lakes = {lake["id"]: lake for lake in load_lakes(min_ha=0.0)}
    replaced = {}
    for item_id in items:
        item = httpx.get(ITEM_URL.format(item_id), timeout=30).json()
        if not suspect(item):
            continue
        targets = [r for r in rows if r["item_id"] == item_id]
        chosen = [lakes[r["lake_id"]] for r in targets if r["lake_id"] in lakes]
        t0 = time.time()
        with rasterio.Env(**GDAL):
            scene = load_archive_scene(item, chosen)
        print(f"\n{item_id}: {len(targets)} rows, offset now "
              f"{scene.extra['offset']:.0f} ({scene.extra['offset_note']}), "
              f"read in {time.time() - t0:.0f} s")
        for old in targets:
            lake = lakes.get(old["lake_id"])
            if lake is None:
                continue
            new, _ = measure_lake(scene, lake)
            if new is None:
                continue
            new = {k: new.get(k, "") for k in fields}
            if old["status"] != new["status"] or old["water_ha"] != str(new["water_ha"]):
                print(f"  {old['lake'][:28]:28} {old['status']:7} {old['water_ha'] or '-':>8} ha"
                      f"  ->  {new['status']:7} {new['water_ha'] or '-':>8} ha")
            replaced[(old["lake_id"], item_id)] = new

    print(f"\n{len(replaced)} rows re-measured.")
    if args.dry_run or not replaced:
        return
    backup = CSV_PATH.with_name(f"{CSV_PATH.stem}.before_offset_fix.csv")
    shutil.copy(CSV_PATH, backup)
    out = [replaced.get((r["lake_id"], r["item_id"]), r) for r in rows]
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out)
    print(f"CSV updated (backup: {backup.name}).")


if __name__ == "__main__":
    main()
