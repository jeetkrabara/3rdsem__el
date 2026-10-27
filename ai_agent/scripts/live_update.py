"""Keep the website live: fetch new Sentinel-2 images and measure the lakes.

Every cycle (default every 6 hours) it
  1. BACKFILL - measures any featured lake (featured_lakes.csv) that is missing
     from an image the other lakes were already measured on (local scenes and
     archive images), so a newly added lake gets the same history as the rest;
  2. NEW IMAGES - searches the AWS Sentinel-2 archive for images newer than the
     newest one measured and measures every featured lake on them;
  3. PICTURES - pre-renders the newest clear image of every lake for the site;
  4. writes reports/lakes/live_status.json (shown on the website).
Rows are appended to reports/lakes/lake_water_timeseries.csv, exactly as
lake_water_timeseries.py writes them; the AI service picks them up by itself.

Run from the project root (internet needed):
    python ai_agent/scripts/live_update.py             # loop forever
    python ai_agent/scripts/live_update.py --once      # one cycle (for cron)
    python ai_agent/scripts/live_update.py --hours 12 --max-cloud 40
Stop with Ctrl+C; it is safe to stop and start again at any time.
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import traceback
from pathlib import Path

import httpx
import rasterio

from lakewater import (ROW_FIELDS, choose_items, load_archive_scene, load_lakes,
                       load_local_scene, measure_lake, search_archive)
from landcover6 import DATA_DIR, find_band, scene_date_key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))          # so ai_agent.app can be imported
CSV_PATH = ROOT / "reports" / "lakes" / "lake_water_timeseries.csv"
STATUS_PATH = ROOT / "reports" / "lakes" / "live_status.json"
TRIED_PATH = ROOT / "reports" / "lakes" / "live_tried.txt"
FEATURED_PATH = ROOT / "featured_lakes.csv"
ITEM_URL = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/{}"
GDAL = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
            GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="3",
            GDAL_HTTP_MULTIPLEX="YES", VSI_CACHE="TRUE", GDAL_CACHEMAX=256)


# ------------------------------------------------------------------ bookkeeping
def featured_lakes():
    with open(FEATURED_PATH, encoding="utf-8") as handle:
        wanted = {row["lake_id"] for row in csv.DictReader(handle)}
    lakes = [lake for lake in load_lakes(min_ha=0.0) if lake["id"] in wanted]
    missing = wanted - {lake["id"] for lake in lakes}
    if missing:
        print(f"Not in the outline file (ignored): {', '.join(sorted(missing))}")
    return lakes


def read_rows():
    if not CSV_PATH.exists():
        return []
    with open(CSV_PATH, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def append_rows(rows):
    new = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROW_FIELDS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerows(rows)


def tried():
    """(lake_id, item_id) pairs already attempted that gave no row (lake
    outside the image), so they are not retried every cycle."""
    if not TRIED_PATH.exists():
        return set()
    return {tuple(line.split()) for line in TRIED_PATH.read_text().splitlines() if line.strip()}


def mark_tried(pairs):
    if pairs:
        with open(TRIED_PATH, "a", encoding="utf-8") as handle:
            handle.writelines(f"{lake} {item}\n" for lake, item in pairs)


def write_status(**fields):
    old = {}
    if STATUS_PATH.exists():
        try:
            old = json.loads(STATUS_PATH.read_text())
        except ValueError:
            pass
    old.update(fields)
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(old, indent=1))
    os.replace(tmp, STATUS_PATH)                 # atomic: the site never reads half a file


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------- measuring
def measure(scene, lakes, done, skipped):
    rows, none = [], []
    for lake in lakes:
        if (lake["id"], scene.item_id) in done:
            continue
        row, _ = measure_lake(scene, lake)
        if row is None:
            none.append((lake["id"], scene.item_id))
        else:
            rows.append(row)
    if rows:
        append_rows(rows)
        done.update((r["lake_id"], r["item_id"]) for r in rows)
    mark_tried(none)
    skipped.update(none)
    return rows


def load_item(item_id):
    for attempt in range(3):
        try:
            return httpx.get(ITEM_URL.format(item_id), timeout=30).json()
        except httpx.HTTPError as error:
            print(f"  {item_id}: {error}; retry {attempt + 1}/3")
            time.sleep(5)
    return None


def archive_scene(item, lakes):
    for attempt in range(3):
        try:
            return load_archive_scene(item, lakes)
        except Exception as error:                 # noqa: BLE001 - network reads
            print(f"  {item['id']}: read failed ({error}); retry {attempt + 1}/3")
            time.sleep(5)
    return None


def backfill(lakes, rows, done, skipped):
    """Measure featured lakes on images the other lakes already have."""
    added = 0
    # local scenes (2015-2016 etc.), read from disk
    local_ids = sorted({r["item_id"] for r in rows if r["source"] == "local"})
    for folder in local_ids:
        need = [l for l in lakes if (l["id"], folder) not in done | skipped]
        if not need or not (DATA_DIR / folder).exists():
            continue
        y, m, d = scene_date_key(folder)
        scene = load_local_scene(DATA_DIR / folder, find_band, dt.date(y, m, d).isoformat())
        if scene is None:
            continue
        got = measure(scene, need, done, skipped)
        added += len(got)
        print(f"  backfill local {folder}: {len(got)} rows for {len(need)} lakes")
    # archive images, the same dates every other lake has
    archive_ids = sorted({(r["date"], r["item_id"]) for r in rows if r["source"] == "archive"})
    for date, item_id in archive_ids:
        need = [l for l in lakes if (l["id"], item_id) not in done | skipped]
        if not need:
            continue
        item = load_item(item_id)
        scene = archive_scene(item, need) if item else None
        if scene is None:
            continue
        got = measure(scene, need, done, skipped)
        added += len(got)
        print(f"  backfill {date} {item_id}: {len(got)} rows for {len(need)} lakes")
        write_status(state="backfilling", rows_added_this_run=added, last_activity=now())
    return added


def new_images(lakes, rows, done, skipped, args):
    """Search the archive for images newer than the newest measured one."""
    newest = max((r["date"] for r in rows if r["source"] == "archive"), default="2017-01-01")
    start = dt.date.fromisoformat(newest) - dt.timedelta(days=args.overlap_days)
    ll = [l["geom_ll"].bounds for l in lakes]
    bbox = (min(b[0] for b in ll), min(b[1] for b in ll), max(b[2] for b in ll), max(b[3] for b in ll))
    items = search_archive(bbox, start.year, dt.date.today().year)
    items = [i for i in items if i["properties"]["datetime"][:10] >= start.isoformat()]
    # every clear-enough image in the recent window (up to 8 per month), oldest first
    chosen, tile = choose_items(items, bbox, per_month=8, max_cloud=args.max_cloud)
    chosen.sort(key=lambda i: i["properties"]["datetime"])
    fresh = [i for i in chosen if any((l["id"], i["id"]) not in done | skipped for l in lakes)]
    print(f"  archive search from {start}: {len(items)} images on/after it, tile {tile}, "
          f"{len(fresh)} not yet measured (cloud < {args.max_cloud:.0f}%)")
    added, dates = 0, []
    for item in fresh:
        need = [l for l in lakes if (l["id"], item["id"]) not in done | skipped]
        scene = archive_scene(item, need)
        if scene is None:
            continue
        got = measure(scene, need, done, skipped)
        added += len(got)
        dates.append(scene.date)
        ok = sum(1 for r in got if r["status"] == "ok")
        print(f"  new {scene.date} {item['id']}: {len(got)} rows ({ok} clear)")
    return added, dates


def prerender(lakes):
    """Newest clear image of each lake, so the website opens instantly."""
    from ai_agent.app import imagery
    rendered = 0
    by_lake = {}
    for r in read_rows():
        if r["status"] == "ok":
            by_lake.setdefault(r["lake_id"], []).append(r)
    for lake in lakes:
        rows = sorted(by_lake.get(lake["id"], []), key=lambda r: r["date"])
        if not rows:
            continue
        try:
            imagery.render(lake["id"], rows[-1]["item_id"])
            rendered += 1
        except Exception as error:                 # noqa: BLE001 - keep going
            print(f"  picture {lake['name']}: {error}")
    return rendered


# ------------------------------------------------------------------------ main
def cycle(args):
    lakes = featured_lakes()
    rows = read_rows()
    done = {(r["lake_id"], r["item_id"]) for r in rows}
    skipped = tried()
    started = now()
    write_status(running=True, state="checking", last_check=started, pid=os.getpid(),
                 interval_hours=args.hours, lakes=len(lakes), error=None)
    print(f"\n[{started}] {len(lakes)} featured lakes, {len(rows):,} rows so far")
    with rasterio.Env(**GDAL):
        filled = backfill(lakes, rows, done, skipped) if not args.no_backfill else 0
        added, dates = new_images(lakes, read_rows(), done, skipped, args)
    pictures = prerender(lakes)
    rows = read_rows()
    newest = max((r["date"] for r in rows if r["status"] == "ok"), default=None)
    next_check = (dt.datetime.now() + dt.timedelta(hours=args.hours)).isoformat(timespec="seconds")
    write_status(running=not args.once, state="idle", last_check=started,
                 last_finished=now(), next_check=None if args.once else next_check,
                 backfilled_rows=filled, new_rows=added, new_image_dates=dates,
                 newest_clear_image=newest, pictures_ready=pictures)
    print(f"Done: {filled} backfilled, {added} new rows from {len(dates)} new images; "
          f"newest clear image {newest}; {pictures} pictures ready.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="one cycle, then exit")
    parser.add_argument("--hours", type=float, default=6.0, help="hours between checks")
    parser.add_argument("--max-cloud", type=float, default=70.0,
                        help="skip images cloudier than this (scene %%)")
    parser.add_argument("--overlap-days", type=int, default=20,
                        help="re-check this many days before the newest image")
    parser.add_argument("--no-backfill", action="store_true")
    args = parser.parse_args()
    try:
        while True:
            try:
                cycle(args)
            except Exception as error:             # noqa: BLE001 - never die in loop mode
                traceback.print_exc()
                write_status(state="error", error=f"{type(error).__name__}: {error}", last_activity=now())
                if args.once:
                    raise
            if args.once:
                break
            print(f"Sleeping {args.hours} h (Ctrl+C to stop)...")
            time.sleep(args.hours * 3600)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        write_status(running=False, state="stopped" if not args.once else "idle")


if __name__ == "__main__":
    main()
