"""Daily rainfall over Bengaluru from CHIRPS v2.0 (satellite + rain-gauge, 0.05 deg ~5 km).

Reads only the Bengaluru window of each day's cloud-optimised GeoTIFF (no big
downloads) and stores the whole grid, so any lake can be looked up later.
Incremental and safe to stop: days already in the file are skipped. CHIRPS
is published about 3-6 weeks after the month ends, so the newest weeks fill
in on later runs (live_update.py calls this every cycle).

Run from the project root:
    python ai_agent/scripts/fetch_rainfall.py              # 2015-01-01 .. latest
    python ai_agent/scripts/fetch_rainfall.py --start 2020-01-01
    python ai_agent/scripts/fetch_rainfall.py --source ee --project aqua-1000
                                    # same CHIRPS data via Google Earth Engine (fast)
    python ai_agent/scripts/fetch_rainfall.py --source era5   # fallback, see below

Fallback (--source era5): ERA5 reanalysis daily rainfall per featured lake from
the Open-Meteo archive API (no key; ~25 km grid; under-counts extreme years).
Output: ai_agent/reports/rainfall/era5_daily_lakes.csv (date, one column per lake).
The attribution uses CHIRPS when its record is complete, otherwise ERA5.
Output: ai_agent/reports/rainfall/chirps_daily_bengaluru.csv
        (date, then one column per grid cell "r<row>c<col>", mm/day)
        ai_agent/reports/rainfall/chirps_grid.json (grid origin and cell size)

Credit: Funk et al. 2015, "The climate hazards infrared precipitation with
stations", Scientific Data 2, 150066. https://www.chc.ucsb.edu/data/chirps
"""

import argparse
import csv
import datetime as dt
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "reports" / "rainfall"
CSV_PATH = OUT_DIR / "chirps_daily_bengaluru.csv"
GRID_PATH = OUT_DIR / "chirps_grid.json"
URL = ("/vsicurl/https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/"
       "cogs/p05/{y}/chirps-v2.0.{y}.{m:02d}.{d:02d}.cog")
BOUNDS = (77.30, 12.75, 77.95, 13.30)      # all Bengaluru lakes, with margin
ENV = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
           CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".cog",
           GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="3")


class Blocked(Exception):
    """The server refused us (HTTP 403/429): stop instead of retrying harder."""


def read_day(day):
    """(day, grid, transform), or (day, None, None) if not published yet."""
    url = URL.format(y=day.year, m=day.month, d=day.day)
    for attempt in range(3):
        try:
            with rasterio.Env(**ENV), rasterio.open(url) as src:
                win = from_bounds(*BOUNDS, src.transform).round_offsets().round_lengths()
                grid = src.read(1, window=win).astype(np.float32)
                transform = src.window_transform(win)
            grid[grid < 0] = np.nan              # CHIRPS uses -9999 for no data
            return day, grid, transform
        except rasterio.errors.RasterioIOError as error:
            msg = str(error)
            if "403" in msg or "429" in msg:
                raise Blocked(msg) from error
            if "404" in msg or "does not exist" in msg:
                return day, None, None
            time.sleep(5 * (attempt + 1))        # transient: back off
    return day, None, None


ERA5_CSV = OUT_DIR / "era5_daily_lakes.csv"
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"


def fetch_era5(start, end):
    """One request per featured lake (polite: one at a time)."""
    import httpx
    from shapely.geometry import shape
    lakes_path = ROOT / "data" / "lakes" / "bengaluru_lakes.geojson"
    with open(ROOT / "featured_lakes.csv", encoding="utf-8") as handle:
        wanted = {row["lake_id"] for row in csv.DictReader(handle)}
    features = [f for f in json.loads(lakes_path.read_text())["features"]
                if f["properties"]["id"] in wanted]
    # ERA5 is a ~0.25 deg grid: lakes in the same cell get identical rain, so
    # request each cell once (Open-Meteo counts long date ranges as many calls)
    cells = {}
    for f in features:
        point = shape(f["geometry"]).representative_point()
        key = (round(point.y * 4) / 4, round(point.x * 4) / 4)
        cells.setdefault(key, []).append(f["properties"]["id"])
    series, dates = {}, None
    for (lat, lon), lake_ids in sorted(cells.items()):
        for attempt in range(6):
            r = httpx.get(OPEN_METEO, timeout=60, params={
                "latitude": lat, "longitude": lon,
                "start_date": start.isoformat(), "end_date": end.isoformat(),
                "daily": "precipitation_sum", "models": "era5", "timezone": "Asia/Kolkata"})
            if r.status_code != 429:
                break
            wait = 30 * (attempt + 1)
            print(f"  rate-limited; waiting {wait} s")
            time.sleep(wait)
        r.raise_for_status()
        daily = r.json()["daily"]
        dates = daily["time"]
        for lake_id in lake_ids:
            series[lake_id] = daily["precipitation_sum"]
        print(f"  cell {lat},{lon}: {len(lake_ids)} lakes")
        time.sleep(10)
    ids = sorted(series)
    with open(ERA5_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date"] + ids)
        for i, day in enumerate(dates):
            values = [series[k][i] for k in ids]
            if all(v is None for v in values):
                continue
            writer.writerow([day] + ["" if v is None else v for v in values])
    print(f"ERA5: {len(ids)} lakes, {len(dates)} days -> {ERA5_CSV}")


CELL = 0.05
GRID_WEST, GRID_NORTH, GRID_ROWS, GRID_COLS = 77.30, 13.30, 11, 13   # CHIRPS-aligned window


def fetch_ee(start, end, project):
    """CHIRPS daily from Earth Engine (UCSB-CHG/CHIRPS/DAILY), one year per request,
    written in the same grid format as the direct download."""
    import ee
    ee.Initialize(project=project)
    region = ee.Geometry.Rectangle([GRID_WEST, GRID_NORTH - GRID_ROWS * CELL,
                                    GRID_WEST + GRID_COLS * CELL, GRID_NORTH])
    have = done_days()
    days = {}
    for year in range(start.year, end.year + 1):
        a = max(start, dt.date(year, 1, 1))
        b = min(end, dt.date(year, 12, 31)) + dt.timedelta(days=1)
        col = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterDate(a.isoformat(), b.isoformat())
        table = col.getRegion(region, 5566).getInfo()
        head, rows = table[0], table[1:]
        i_id, i_lon, i_lat, i_p = (head.index(k) for k in ("id", "longitude", "latitude", "precipitation"))
        n = 0
        for r in rows:
            day = dt.datetime.strptime(r[i_id][:8], "%Y%m%d").date().isoformat()
            if day in have:
                continue
            row = int((GRID_NORTH - r[i_lat]) / CELL)
            c = int((r[i_lon] - GRID_WEST) / CELL)
            if 0 <= row < GRID_ROWS and 0 <= c < GRID_COLS:
                grid = days.setdefault(day, np.full((GRID_ROWS, GRID_COLS), np.nan, np.float32))
                grid[row, c] = np.nan if r[i_p] is None or r[i_p] < 0 else r[i_p]
                n += 1
        print(f"  {year}: {len(rows):,} values")
    if not GRID_PATH.exists():
        GRID_PATH.write_text(json.dumps({"west": GRID_WEST, "north": GRID_NORTH, "cell_deg": CELL,
                                         "rows": GRID_ROWS, "cols": GRID_COLS,
                                         "source": "CHIRPS v2.0 daily (Earth Engine)"}))
    _save({dt.date.fromisoformat(d): g for d, g in days.items()})
    print(f"Stored {len(days)} new days (newest {max(days) if days else '-'}).")


def done_days():
    if not CSV_PATH.exists():
        return set()
    with open(CSV_PATH, encoding="utf-8") as handle:
        return {row[0] for row in csv.reader(handle)} - {"date"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="", help="default: today")
    parser.add_argument("--source", choices=["chirps", "ee", "era5"], default="chirps")
    parser.add_argument("--project", default=os.environ.get("EE_PROJECT", ""),
                        help="Earth Engine project id for --source ee")
    parser.add_argument("--workers", type=int, default=4,
                        help="parallel downloads; keep this small, the server rate-limits")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    if args.source == "era5":
        fetch_era5(start, end)
        return
    if args.source == "ee":
        if not args.project:
            raise SystemExit("--source ee needs --project (or EE_PROJECT).")
        fetch_ee(start, end, args.project)
        return
    have = done_days()
    todo = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    todo = [d for d in todo if d.isoformat() not in have]
    print(f"{len(have)} days stored; fetching {len(todo)} ({start}..{end})")
    if not todo:
        return

    results, missing = {}, []
    blocked = None
    with ThreadPoolExecutor(args.workers) as pool:
        try:
            for n, (day, grid, transform) in enumerate(pool.map(read_day, todo), 1):
                _keep(results, missing, day, grid, transform, n, len(todo))
        except Blocked as error:
            blocked = error
            pool.shutdown(cancel_futures=True)
    _save(results)
    if blocked:
        print(f"Stopped: the CHIRPS server refused requests ({blocked}). Saved "
              f"{len(results)} days; wait a few hours and run again (it resumes).")
        return
    published = [d for d in missing if d < end - dt.timedelta(days=60)]
    print(f"Stored {len(results)} new days. Not published yet: {len(missing)} "
          f"(newest stored: {max(results).isoformat() if results else '-'})")
    if published:
        print(f"Warning: {len(published)} older days failed to download "
              f"(e.g. {published[0]}); run again to retry.")


def _keep(results, missing, day, grid, transform, n, total):
    if grid is None:
        missing.append(day)
    else:
        results[day] = grid
        if not GRID_PATH.exists():
            GRID_PATH.write_text(json.dumps({
                "west": transform.c, "north": transform.f,
                "cell_deg": transform.a, "rows": grid.shape[0],
                "cols": grid.shape[1], "source": "CHIRPS v2.0 daily p05"}))
    if n % 250 == 0:
        print(f"  {n}/{total}")


def _save(results):
    if not results:
        return
    shape = next(iter(results.values())).shape
    new = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["date"] + [f"r{r}c{c}" for r in range(shape[0])
                                        for c in range(shape[1])])
        for day in sorted(results):
            writer.writerow([day.isoformat()] + [
                "" if np.isnan(v) else round(float(v), 2) for v in results[day].ravel()])



if __name__ == "__main__":
    main()
