"""Real satellite pictures of one lake, year by year, for the website.

For every year the lake was measured we pick ONE representative image (the
clearest pre-monsoon date, Feb-May, so years are comparable) and, on request,
re-open that exact Sentinel-2 image (AWS archive, or the local scene files),
re-run the same measurement as lake_water_timeseries.py and save:

    truecolor.png   natural colour (fixed stretch, so years look comparable)
    overlay.png     the same picture + OSM outline (yellow) + measured water edge (cyan)
    mndwi.png       the water index the measurement thresholds, coloured around
                    that date's threshold (blue = counted as water)
    meta.json       numbers, MNDWI histogram and threshold (the "why" of the area)

Files are cached under reports/web/<lake_id>/ so each image is fetched once.
Nothing is invented: if an image cannot be opened the error is returned.
"""

import json
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import lake_analytics as la

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
WEB_DIR = Path(__file__).resolve().parents[1] / "reports" / "web"
STAC_ITEM_URL = ("https://earth-search.aws.element84.com/v1/collections/"
                 "{collection}/items/{item_id}")
COLLECTIONS = ("sentinel-2-l2a", "sentinel-2-c1-l2a")
SEASON_MONTHS = la.SEASON_MONTHS           # Feb-May: pre-monsoon, comparable
RENDER_VERSION = 2                         # bump to re-render cached pictures
UPSCALE = 3                                # display only; data stays 10-20 m
EDGE_PX = 2                                # line width on the enlarged picture
WATER_FILL = (0, 200, 255)
WATER_ALPHA = 0.35
TRUECOLOR_MAX = 0.22                       # reflectance shown as full white
GAMMA = 0.85
HIST_BINS = np.linspace(-1, 1, 81)
GDAL_OPTIONS = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
    GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="3",
    GDAL_HTTP_MULTIPLEX="YES", VSI_CACHE="TRUE", GDAL_CACHEMAX=256,
)


class ImageryError(Exception):
    pass


def _lw():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import lakewater
    return lakewater


@lru_cache(maxsize=1)
def _lw_lakes():
    """Lake dicts in lakewater's own format (also sets its neighbour list)."""
    return {lake["id"]: lake for lake in _lw().load_lakes(min_ha=0.0)}


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------ choosing dates
def _season_score(d):
    """Lower is better: pre-monsoon first, then closest to 1 April."""
    pre = d.month in SEASON_MONTHS
    return (0 if pre else 1, abs((d - date(d.year, 4, 1)).days))


def year_picks(lake_id):
    """One representative measured image per year (plus every usable date)."""
    rows = la._all_rows().get(lake_id, [])
    by_year = {}
    for r in rows:
        by_year.setdefault(r["_date"].year, []).append(r)
    out = []
    for year in sorted(by_year):
        usable = [r for r in by_year[year] if r["status"] in ("ok", "small")]
        cloudy = sum(1 for r in by_year[year] if r["status"] == "cloudy")
        if not usable:
            out.append({"year": year, "missing": True, "cloudy_dates": cloudy,
                        "reason": "every image this year was too cloudy over the lake"})
            continue
        # outline nearly cloud-free, then pre-monsoon, then a clear scene overall,
        # then the sharper 10 m archive image over 17-20 m local scenes
        def rank(r):
            cloud = _num(r.get("scene_cloud_pct"))
            pre, days = _season_score(r["_date"])
            return (0 if (_num(r["valid_frac"]) or 0) >= 0.95 else 1, pre,
                    0 if cloud is None or cloud <= 20 else 1,
                    0 if r["source"] == "archive" else 1, days)
        best = min(usable, key=rank)
        out.append({
            "year": year, "missing": False, **_row_view(best),
            "pre_monsoon": best["_date"].month in SEASON_MONTHS,
            "cloudy_dates": cloudy,
            "other_dates": [_row_view(r) for r in usable if r is not best],
        })
    return out


def _row_view(r):
    return {"date": r["_date"].isoformat(), "source": r["source"],
            "item_id": r["item_id"], "status": r["status"],
            "open_water_ha": _num(r.get("water_ha")),
            "vegetation_ha": _num(r.get("vegetation_ha")),
            "valid_frac": _num(r.get("valid_frac")),
            "pixel_m": _num(r.get("pixel_m"))}


# ------------------------------------------------------------- scene loading
def _stac_item(item_id):
    import httpx
    for collection in COLLECTIONS:
        r = httpx.get(STAC_ITEM_URL.format(collection=collection, item_id=item_id),
                      timeout=30)
        if r.status_code == 200:
            return r.json()
    raise ImageryError(f"Sentinel-2 item {item_id} not found in the AWS archive.")


def _load(row_source, item_id, date_text, lake):
    """(SceneData, blue reflectance aligned to it)."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import array_bounds
    from rasterio.windows import from_bounds
    lw = _lw()
    if row_source == "local":
        from landcover6 import DATA_DIR, find_band
        folder = DATA_DIR / item_id
        scene = lw.load_local_scene(folder, find_band, date_text)
        if scene is None:
            raise ImageryError(f"Local scene {item_id} could not be read.")
        with rasterio.open(find_band(folder, "B02")) as src:
            blue = src.read(1).astype(np.float32)
        scale = 10000.0 if np.nanpercentile(blue, 99) > 5 else 1.0
        return scene, blue / scale
    item = _stac_item(item_id)
    with rasterio.Env(**GDAL_OPTIONS):
        scene = lw.load_archive_scene(item, [lake])
        if scene is None:
            raise ImageryError("The lake is outside this archive image.")
        h, w = scene.green.shape
        bounds = array_bounds(h, w, scene.transform)
        with rasterio.open(item["assets"]["blue"]["href"]) as src:
            blue_dn = src.read(1, window=from_bounds(*bounds, transform=src.transform),
                               out_shape=(h, w), resampling=Resampling.bilinear)
    return scene, lw.to_reflectance(blue_dn, scene.extra["offset"])


def _window(lw, scene, lake):
    """The same sub-window measure_lake() uses, so pictures and numbers match."""
    from rasterio import windows
    geom = lw.lake_geom(lake, scene.crs)
    h, w = scene.green.shape
    win = windows.from_bounds(*geom.buffer(max(lw.RING_M, lw.BODY_REACH_M)).bounds,
                              transform=scene.transform)
    win = win.round_offsets(op="floor").round_lengths(op="ceil")
    return win.intersection(windows.Window(0, 0, w, h)).toslices()


# ------------------------------------------------------------------ drawing
def _truecolor(red, green, blue):
    rgb = np.dstack([red, green, blue])
    rgb = np.clip(np.nan_to_num(rgb) / TRUECOLOR_MAX, 0, 1) ** GAMMA
    return (rgb * 255).astype(np.uint8)


def _edge(mask):
    from scipy import ndimage
    return mask & ~ndimage.binary_erosion(mask, iterations=1)


def _upscale(arr):
    return np.repeat(np.repeat(arr, UPSCALE, axis=0), UPSCALE, axis=1)


def _smooth_upscale(rgb):
    """Smooth enlargement for display (like the QA chips); data is unchanged."""
    from PIL import Image
    h, w = rgb.shape[:2]
    return np.asarray(Image.fromarray(rgb).resize((w * UPSCALE, h * UPSCALE), Image.LANCZOS))


def _thick_edge(mask):
    from scipy import ndimage
    return ndimage.binary_dilation(_edge(mask), iterations=EDGE_PX - 1) if EDGE_PX > 1 else _edge(mask)


def _overlay(rgb, inside, water):
    big = _smooth_upscale(rgb).astype(np.float32)
    w = _upscale(water)
    big[w] = big[w] * (1 - WATER_ALPHA) + np.array(WATER_FILL) * WATER_ALPHA   # counted water
    big = big.astype(np.uint8)
    big[_thick_edge(_upscale(inside))] = (255, 225, 60)   # OSM outline
    big[_thick_edge(w)] = (0, 235, 255)                    # measured water edge
    return big


def _mndwi_png(mndwi, threshold, clear):
    from matplotlib import colormaps
    # centre the colour scale on this date's threshold: blue side = counted water
    span = 0.5
    norm = np.clip((mndwi - threshold) / (2 * span) + 0.5, 0, 1)
    rgb = (colormaps["RdYlBu"](norm)[..., :3] * 255).astype(np.uint8)
    rgb[~clear] = (128, 128, 128)
    return _upscale(rgb)


def _save(arr, path):
    from PIL import Image
    Image.fromarray(arr).save(path, optimize=True)


# ------------------------------------------------------------------- render
def render(lake_id, item_id):
    """Render (or return cached) pictures + measurement for one lake image."""
    out_dir = WEB_DIR / lake_id
    meta_path = out_dir / f"{item_id}.json"
    if meta_path.exists():
        cached = json.loads(meta_path.read_text())
        if cached.get("render_version") == RENDER_VERSION:
            return cached

    rows = [r for r in la._all_rows().get(lake_id, []) if r["item_id"] == item_id]
    if not rows:
        raise ImageryError(f"{item_id} was never measured for {lake_id}.")
    stored = rows[0]
    lake = _lw_lakes().get(lake_id)
    if lake is None:
        raise ImageryError(f"Unknown lake {lake_id}.")

    lw = _lw()
    scene, blue = _load(stored["source"], item_id, stored["_date"].isoformat(), lake)
    row, debug = lw.measure_lake(scene, lake, keep_debug=True)
    if row is None:
        raise ImageryError("The lake is outside this image.")
    if debug is None:
        raise ImageryError(f"Image is {row.get('status')} over the lake "
                           f"({row.get('valid_frac')} of the outline clear).")
    rows_sl, cols_sl = _window(lw, scene, lake)
    blue = blue[rows_sl, cols_sl]
    rgb = _truecolor(debug["red"], debug["green"], blue)

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{lake_id}/{item_id}"
    _save(_smooth_upscale(rgb), out_dir / f"{item_id}_truecolor.png")
    _save(_overlay(rgb, debug["inside"], debug["water"]), out_dir / f"{item_id}_overlay.png")
    threshold = float(row["threshold"])
    _save(_mndwi_png(debug["mndwi"], threshold, debug["clear"]), out_dir / f"{item_id}_mndwi.png")

    sample_inside = debug["mndwi"][debug["inside"] & debug["clear"]]
    sample_ring = debug["mndwi"][debug["ring"] & debug["clear"]]
    hist_in, _ = np.histogram(sample_inside, HIST_BINS)
    hist_ring, _ = np.histogram(sample_ring, HIST_BINS)
    centres = (HIST_BINS[:-1] + HIST_BINS[1:]) / 2
    stored_ha = _num(stored.get("water_ha"))
    meta = {
        "render_version": RENDER_VERSION,
        "offset_note": (scene.extra or {}).get("offset_note"),
        "lake_id": lake_id, "lake": row["lake"], "item_id": item_id,
        "date": row["date"], "source": row["source"],
        "satellite": "Sentinel-2 L2A (AWS Earth Search archive)" if row["source"] == "archive"
                     else "Sentinel-2 L2A (project's downloaded scene)",
        "pixel_m": row["pixel_m"], "scene_cloud_pct": row.get("scene_cloud_pct") or None,
        "outline_ha": row["outline_ha"], "valid_frac": row["valid_frac"],
        "open_water_ha": row["water_ha"], "stored_open_water_ha": stored_ha,
        "matches_stored": stored_ha is not None and abs(stored_ha - row["water_ha"]) < 0.5,
        "water_pct_of_outline": row["water_pct_of_outline"],
        "vegetation_ha": _num(row.get("vegetation_ha")),
        "threshold": threshold, "threshold_method": row["threshold_method"],
        "rule": (f"water = MNDWI > {threshold:.3f} ({row['threshold_method']}) AND "
                 f"near-infrared < {lw.NIR_MAX}, counted inside the outline +{lw.BUFFER_M:.0f} m"),
        "histogram": [{"mndwi": round(float(c), 3), "inside": int(a), "around": int(b)}
                      for c, a, b in zip(centres, hist_in, hist_ring)],
        "images": {k: f"/files/web/{stem}_{k}.png?v={RENDER_VERSION}" for k in ("truecolor", "overlay", "mndwi")},
    }
    meta_path.write_text(json.dumps(meta))
    return meta
