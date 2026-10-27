"""Daily rainfall per lake for the analyses (CHIRPS when complete, else ERA5).

CHIRPS v2.0 daily (satellite + rain gauges, ~5 km) comes from
scripts/fetch_rainfall.py (direct download or Google Earth Engine); a lake gets
the mean of the 3x3 cells (~15 km) around it. ERA5 (Open-Meteo, ~25 km) is a
provisional fallback that under-counts extreme years.
Credit: Funk et al. 2015, Scientific Data 2:150066.
"""

import csv
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAIN_CSV = ROOT / "reports" / "rainfall" / "chirps_daily_bengaluru.csv"
GRID_JSON = ROOT / "reports" / "rainfall" / "chirps_grid.json"
ERA5_CSV = ROOT / "reports" / "rainfall" / "era5_daily_lakes.csv"
RAIN_START = date(2015, 1, 1)
CHIRPS_MIN_COVERAGE = 0.95          # use CHIRPS only when its record is ~complete


class RainfallUnavailable(Exception):
    pass


@lru_cache(maxsize=2)
def _rain_grid(stamp):
    """(dates, array[day, cell]) of CHIRPS daily rainfall; stamp = file mtime."""
    if not RAIN_CSV.exists() or not GRID_JSON.exists():
        raise RainfallUnavailable(
            "No rainfall data yet. Run: python ai_agent/scripts/fetch_rainfall.py")
    with open(RAIN_CSV, encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        rows = sorted(reader, key=lambda r: r[0])
    days = [date.fromisoformat(r[0]) for r in rows]
    values = np.array([[float(v) if v else np.nan for v in r[1:]] for r in rows], dtype=np.float32)
    return days, values


def _chirps_complete():
    if not (RAIN_CSV.exists() and GRID_JSON.exists()):
        return False
    days, _ = _rain_grid(RAIN_CSV.stat().st_mtime)
    span = (days[-1] - RAIN_START).days + 1
    current = days[-1] >= date.today() - timedelta(days=90)     # CHIRPS lags ~1-2 months
    return (current and days[0] <= RAIN_START + timedelta(days=5)
            and len(days) / span >= CHIRPS_MIN_COVERAGE)


@lru_cache(maxsize=2)
def _era5(stamp):
    with open(ERA5_CSV, encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = list(reader)
    return header[1:], [date.fromisoformat(r[0]) for r in rows], rows


def rain_source():
    if _chirps_complete():
        return "CHIRPS v2.0 daily (satellite + gauges, ~5 km), 3x3 cells (~15 km) around the lake"
    if ERA5_CSV.exists():
        return ("ERA5 reanalysis daily (Open-Meteo, ~25 km) - provisional: coarser than CHIRPS and "
                "under-counts extreme years")
    raise RainfallUnavailable(
        "No rainfall data yet. Run: python ai_agent/scripts/fetch_rainfall.py "
        "(or --source era5 as a fallback)")


def lake_rain(lake):
    """Daily rainfall (mm) for a lake on a continuous daily calendar (gaps NaN).
    CHIRPS (mean of 3x3 cells) when complete, else ERA5 at the lake."""
    if not _chirps_complete():
        rain_source()                               # raises if there is nothing at all
        ids, days, rows = _era5(ERA5_CSV.stat().st_mtime)
        if lake["id"] not in ids:
            raise RainfallUnavailable("No ERA5 rainfall for this lake; re-run fetch_rainfall.py --source era5.")
        col = ids.index(lake["id"]) + 1
        start, end = days[0], days[-1]
        lookup = {d: float(r[col]) if r[col] else np.nan for d, r in zip(days, rows)}
        calendar = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        return calendar, np.array([lookup.get(d, np.nan) for d in calendar])
    days, values = _rain_grid(RAIN_CSV.stat().st_mtime)
    grid = json.loads(GRID_JSON.read_text())
    point = lake["geom"].representative_point()
    row = int((grid["north"] - point.y) / grid["cell_deg"])
    col = int((point.x - grid["west"]) / grid["cell_deg"])
    cells = [r * grid["cols"] + c
             for r in range(max(row - 1, 0), min(row + 2, grid["rows"]))
             for c in range(max(col - 1, 0), min(col + 2, grid["cols"]))]
    per_day = np.nanmean(values[:, cells], axis=1)
    start, end = days[0], days[-1]
    calendar = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    lookup = dict(zip(days, per_day))
    return calendar, np.array([lookup.get(d, np.nan) for d in calendar], dtype=np.float64)
