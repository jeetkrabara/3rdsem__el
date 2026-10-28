"""What is happening to the land around each lake, year by year.

For the same representative image per year as the "images by year" tab, run the
land-cover U-Net on a window around the lake and measure the four classes in
zones that follow the shoreline (OpenStreetMap outline):

    inside      the lakebed: water, weeds/vegetation, dry bed (bare), built-up
    0-30 m      the protected buffer (KTCDA: 30 m around lakes)
    30-75 m     the wider buffer (75 m in the 2016 NGT order)
    75-200 m    the neighbourhood

The main output is built-up land inside the buffers over the years: evidence
of encroachment. Results are cached per lake, image and model file.

Limits: pixels are 10 m (archive) or ~17-20 m (2015-16 local scenes), so the
0-30 m ring is only 1-3 pixels wide and mixed pixels are common - read trends
over several years, not a single year. The model is ~85% accurate against
WorldCover overall, weaker on bare land; built-up next to water can be confused.
"""

import json
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import imagery
from . import lake_analytics as la

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CACHE_DIR = Path(__file__).resolve().parents[1] / "reports" / "web" / "rings"
RINGS = [("inside", None, None), ("0-30 m", 0, 30), ("30-75 m", 30, 75), ("75-200 m", 75, 200)]
MARGIN_M = 260
CLASSES = ["water", "vegetation", "bare_land", "built_up"]
CLASS_COLORS = {0: (0, 100, 200), 1: (40, 160, 60), 2: (200, 170, 100), 3: (220, 60, 60)}


class RingsUnavailable(Exception):
    pass


def _scripts():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))


@lru_cache(maxsize=1)
def _model():
    _scripts()
    import landcover6
    return landcover6.load_model()


def _model_stamp():
    _scripts()
    import landcover6
    p = landcover6.MODEL_PATH
    return f"{p.name}-{int(p.stat().st_mtime)}"


def _features_archive(item_id, geom_utm, crs):
    """9 model inputs for a window around the lake from an archive image."""
    _scripts()
    from rasterio.transform import from_origin
    import build_archive_training as bat
    item = imagery._stac_item(item_id)
    minx, miny, maxx, maxy = geom_utm.buffer(MARGIN_M).bounds
    transform = from_origin(minx, maxy, 10.0, 10.0)
    shape = (int((maxy - miny) // 10), int((maxx - minx) // 10))
    import rasterio
    with rasterio.Env(**bat.GDAL):
        dn = {b: bat.read_band(item["assets"][a]["href"], crs, transform, shape, bat.Resampling.bilinear)
              for b, a in bat.ASSETS.items()}
        scl = bat.read_band(item["assets"]["scl"]["href"], crs, transform, shape, bat.Resampling.nearest)
    usable = ~np.isin(scl, bat.SCL_BAD) & (dn["B03"] > 0)
    offset, _ = bat.decide_offset(item["properties"], dn["B11"], usable, dn["B03"])
    refl = {b: np.nan_to_num(bat.to_reflectance(v, offset), nan=0.0) for b, v in dn.items()}
    ch = [bat.fixed_scale(refl[b], bat.FIXED_RANGE[b]) for b in bat.BANDS]
    ch.append(bat.index_scaled(refl["B08"] - refl["B04"], refl["B08"] + refl["B04"]))
    ch.append(bat.index_scaled(refl["B03"] - refl["B08"], refl["B03"] + refl["B08"]))
    ch.append(bat.index_scaled(refl["B11"] - refl["B08"], refl["B11"] + refl["B08"]))
    feat = np.stack(ch).astype(np.float32)
    feat[:, ~usable] = 0.0
    return feat, usable, transform, 10.0


def _features_local(scene_name, geom_utm_fn):
    """Same, cropped from a downloaded local scene (~17-20 m)."""
    _scripts()
    import landcover6
    from rasterio.windows import Window, from_bounds
    from rasterio import windows as rwin
    features, valid, profile, reason = landcover6.load_scene(landcover6.DATA_DIR / scene_name)
    if features is None:
        raise RingsUnavailable(f"Scene {scene_name}: {reason}")
    geom = geom_utm_fn(profile["crs"])
    win = from_bounds(*geom.buffer(MARGIN_M).bounds, transform=profile["transform"])
    win = win.round_offsets(op="floor").round_lengths(op="ceil").intersection(
        Window(0, 0, features.shape[2], features.shape[1]))
    rows, cols = win.toslices()
    transform = rwin.transform(win, profile["transform"])
    return (features[:, rows, cols], valid[rows, cols], transform,
            float(abs(profile["transform"].a)), geom)


def _zones(geom, shape, transform):
    from rasterio import features as rfeat
    masks, prev = {}, None
    for name, lo, hi in RINGS:
        if name == "inside":
            shp = geom
        else:
            shp = geom.buffer(hi).difference(geom.buffer(lo) if lo else geom)
        masks[name] = rfeat.rasterize([(shp, 1)], out_shape=shape, transform=transform,
                                      fill=0, dtype="uint8", all_touched=False).astype(bool)
    return masks


def _measure(lake_id, pick):
    from rasterio.warp import transform_geom
    from shapely.geometry import mapping, shape as shp_shape
    lake = imagery._lw_lakes().get(lake_id)
    if lake is None:
        raise RingsUnavailable(f"Unknown lake {lake_id}.")

    def geom_in(crs):
        return shp_shape(transform_geom("EPSG:4326", crs, mapping(lake["geom_ll"])))

    if pick["source"] == "archive":
        item = imagery._stac_item(pick["item_id"])
        import rasterio
        with rasterio.Env(**imagery.GDAL_OPTIONS), rasterio.open(item["assets"]["green"]["href"]) as src:
            crs = src.crs
        geom = geom_in(crs)
        feat, usable, transform, px = _features_archive(pick["item_id"], geom, crs)
    else:
        feat, usable, transform, px, geom = _features_local(pick["item_id"], geom_in)
    _scripts()
    import landcover6
    labels = landcover6.predict_scene(_model(), feat, usable)
    masks = _zones(geom, labels.shape, transform)
    px_ha = px * px / 10_000
    rings = {}
    for name, m in masks.items():
        ok = m & usable
        counts = np.bincount(labels[ok], minlength=256)[:4]
        rings[name] = {**{c: round(float(counts[i] * px_ha), 2) for i, c in enumerate(CLASSES)},
                       "clear_frac": round(float(ok.sum() / max(m.sum(), 1)), 3),
                       "pixels": int(m.sum())}
    # picture of the class map with the zone edges, for the website
    from PIL import Image
    rgb = np.full(labels.shape + (3,), 128, dtype=np.uint8)
    for k, c in CLASS_COLORS.items():
        rgb[(labels == k) & usable] = c
    edge = np.zeros(labels.shape, bool)
    from scipy import ndimage
    for m in masks.values():
        edge |= m & ~ndimage.binary_erosion(m)
    rgb[edge] = (255, 255, 255)
    scale = max(1, int(round(30 / px)))                 # ~3 px per 10 m for display
    img = Image.fromarray(rgb).resize((rgb.shape[1] * scale, rgb.shape[0] * scale), Image.NEAREST)
    out_dir = CACHE_DIR / lake_id
    out_dir.mkdir(parents=True, exist_ok=True)
    img.save(out_dir / f"{pick['item_id']}_classes.png", optimize=True)
    return {"year": pick["year"], "date": pick["date"], "source": pick["source"],
            "item_id": pick["item_id"], "pixel_m": px, "rings": rings,
            "map": f"/files/web/rings/{lake_id}/{pick['item_id']}_classes.png"}


def lake_rings(lake_id):
    """Per-year zone measurements for a lake (cached per image and model)."""
    stamp = _model_stamp()
    cache = CACHE_DIR / lake_id / f"rings_{stamp}.json"
    done = json.loads(cache.read_text()) if cache.exists() else {}
    years, errors = [], []
    for pick in imagery.year_picks(lake_id):
        if pick["missing"]:
            continue
        key = pick["item_id"]
        if key not in done:
            try:
                done[key] = _measure(lake_id, pick)
            except Exception as error:                  # noqa: BLE001 - one image never stops the rest
                errors.append({"year": pick["year"], "date": pick["date"], "error": str(error)[:200]})
                continue
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(done))
        years.append(done[key])
    years.sort(key=lambda y: y["year"])
    lake = la._lake_by_id(lake_id)
    return {"lake_id": lake_id, "name": lake["name"] if lake else lake_id,
            "model": stamp, "zones": [r[0] for r in RINGS], "years": years,
            "change": _change(years), "errors": errors,
            "note": ("Land cover from the U-Net on one clear image per year. The 0-30 m ring is only "
                     "1-3 pixels wide, so read multi-year trends rather than single years.")}


def _change(years):
    """Built-up and vegetation change in each zone, first vs last 3 years (averaged)."""
    if len(years) < 4:
        return None
    first, last = years[:3], years[-3:]
    out = {}
    for zone in [r[0] for r in RINGS]:
        def avg(rows, c):
            vals = [y["rings"][zone][c] for y in rows if y["rings"][zone]["clear_frac"] >= 0.8]
            return float(np.mean(vals)) if vals else None
        z = {}
        for c in CLASSES:
            a, b = avg(first, c), avg(last, c)
            z[c] = None if a is None or b is None else {"from_ha": round(a, 2), "to_ha": round(b, 2),
                                                        "change_ha": round(b - a, 2)}
        out[zone] = z
    return {"from_years": [y["year"] for y in first], "to_years": [y["year"] for y in last], "zones": out}
