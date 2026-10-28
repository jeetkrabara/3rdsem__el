"""Build a multi-season 10 m training set for the land-cover U-Net.

Why: the current model learned from only two April scenes (2020, 2021), so it
is good in April but weaker in other seasons (78.6% on October 2026) and it can
only run on the downloaded scenes. ESA WorldCover gives labels for 2020 and
2021, so ANY clear Sentinel-2 image from those years can be a training image.

What it does:
  1. searches the AWS Sentinel-2 L2A archive for clear images (cloud < 5% over
     the area) in 2020 and 2021, keeping about one per month across the year;
  2. reads the 6 bands at 10 m over the same area as the local scenes, applies
     the reflectance offset check, masks clouds / shadow with the SCL band;
  3. builds the SAME 9 model inputs as landcover6.load_scene (fixed scaling +
     NDVI, NDWI, NDBI);
  4. reprojects WorldCover (2020 v100 or 2021 v200) onto that 10 m grid with the
     same class mapping as evaluate_vs_worldcover.py;
  5. saves data/training_archive/<date>.npz (features float16, labels uint8).

Run from the project root (internet needed; ~2-5 min per image):
    python ai_agent/scripts/build_archive_training.py            # 2020-2021
    python ai_agent/scripts/build_archive_training.py --per-month 1 --max-cloud 5
Safe to stop and re-run: finished dates are skipped.
"""

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio import windows
from rasterio.enums import Resampling
from rasterio.warp import reproject

from evaluate_vs_worldcover import LUT as WC_LUT
from lakewater import choose_items, decide_offset, search_archive, to_reflectance
from landcover6 import BANDS, FIXED_RANGE, IGNORE, ROOT, WC_DIR, fixed_scale, index_scaled

OUT_DIR = ROOT / "data" / "training_archive"
# Same area as the local scenes (UTM 43N), so both data sets cover the same city
REFERENCE_GRID = WC_DIR / "reference_3april21_worldcover.tif"
PIXEL_M = 10.0
ASSETS = {"B02": "blue", "B03": "green", "B04": "red", "B08": "nir", "B11": "swir16", "B12": "swir22"}
WORLDCOVER = {2020: WC_DIR / "ESA_WorldCover_10m_2020_v100_N12E075_Map.tif",
              2021: WC_DIR / "ESA_WorldCover_10m_2021_v200_N12E075_Map.tif"}
SCL_BAD = (0, 1, 3, 8, 9, 10)          # no data, saturated, cloud shadow, clouds, cirrus
MIN_CLEAR = 0.85                       # skip images with less of the area clear
GDAL = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
            GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="3",
            GDAL_HTTP_MULTIPLEX="YES", VSI_CACHE="TRUE", GDAL_CACHEMAX=512)


def target_grid():
    """10 m grid over the local scenes' area: (crs, transform, shape, lon/lat bbox)."""
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds
    with rasterio.open(REFERENCE_GRID) as src:
        left, bottom, right, top = src.bounds
        crs = src.crs
    width = int((right - left) // PIXEL_M)
    height = int((top - bottom) // PIXEL_M)
    transform = from_origin(left, top, PIXEL_M, PIXEL_M)
    return crs, transform, (height, width), transform_bounds(crs, "EPSG:4326", left, bottom, right, top)


def read_band(href, crs, transform, shape, resampling):
    """Read an archive band onto the target grid (same UTM zone: a window read)."""
    with rasterio.open(href) as src:
        if src.crs != crs:
            raise ValueError(f"unexpected CRS {src.crs}")
        bounds = windows.bounds(windows.Window(0, 0, shape[1], shape[0]), transform)
        win = windows.from_bounds(*bounds, transform=src.transform)
        return src.read(1, window=win, out_shape=shape, resampling=resampling,
                        boundless=True, fill_value=0)


def build_item(item, crs, transform, shape):
    props, assets = item["properties"], item["assets"]
    dn = {b: read_band(assets[a]["href"], crs, transform, shape,
                       Resampling.bilinear) for b, a in ASSETS.items()}
    scl = read_band(assets["scl"]["href"], crs, transform, shape, Resampling.nearest)
    usable = ~np.isin(scl, SCL_BAD) & (dn["B03"] > 0)
    if usable.mean() < MIN_CLEAR:
        return None, f"only {usable.mean():.0%} of the area clear"
    offset, note = decide_offset(props, dn["B11"], usable, dn["B03"])
    refl = {b: np.nan_to_num(to_reflectance(v, offset), nan=0.0) for b, v in dn.items()}
    channels = [fixed_scale(refl[b], FIXED_RANGE[b]) for b in BANDS]
    channels.append(index_scaled(refl["B08"] - refl["B04"], refl["B08"] + refl["B04"]))   # NDVI
    channels.append(index_scaled(refl["B03"] - refl["B08"], refl["B03"] + refl["B08"]))   # NDWI
    channels.append(index_scaled(refl["B11"] - refl["B08"], refl["B11"] + refl["B08"]))   # NDBI
    features = np.stack(channels).astype(np.float32)
    features[:, ~usable] = 0.0
    return (features, usable, offset, note), None


def worldcover_labels(year, crs, transform, shape):
    wc = np.zeros(shape, dtype=np.uint8)
    with rasterio.open(WORLDCOVER[year]) as src:
        reproject(source=rasterio.band(src, 1), destination=wc, dst_transform=transform,
                  dst_crs=crs, resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
    labels = WC_LUT[wc]
    labels[wc == 0] = IGNORE
    return labels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", default="2020,2021")
    parser.add_argument("--per-month", type=int, default=1)
    parser.add_argument("--max-cloud", type=float, default=5.0, help="scene cloud %% limit")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    crs, transform, shape, bbox = target_grid()
    years = [int(y) for y in args.years.split(",")]
    print(f"Target grid: {shape[1]} x {shape[0]} px at {PIXEL_M:.0f} m over {[round(v, 3) for v in bbox]}")
    labels_by_year = {y: worldcover_labels(y, crs, transform, shape) for y in years}

    with rasterio.Env(**GDAL):
        items = search_archive(bbox, min(years), max(years))
        chosen, tile = choose_items(items, bbox, args.per_month, args.max_cloud)
        chosen = [i for i in chosen if int(i["properties"]["datetime"][:4]) in years]
        print(f"{len(items)} archive images found; tile {tile}; {len(chosen)} chosen "
              f"(<= {args.per_month}/month, cloud < {args.max_cloud}%)")
        for item in sorted(chosen, key=lambda i: i["properties"]["datetime"]):
            day = item["properties"]["datetime"][:10]
            out = OUT_DIR / f"{day}.npz"
            if out.exists():
                print(f"  {day}: done earlier")
                continue
            t0 = time.time()
            try:
                built, why = build_item(item, crs, transform, shape)
            except Exception as error:                     # noqa: BLE001 - network reads
                print(f"  {day}: read failed ({error})")
                continue
            if built is None:
                print(f"  {day}: skipped, {why}")
                continue
            features, usable, offset, note = built
            labels = labels_by_year[int(day[:4])].copy()
            labels[~usable] = IGNORE
            tmp = out.with_suffix(".partial.npz")      # renamed when complete, so readers never see half a file
            np.savez_compressed(tmp, features=features.astype(np.float16), labels=labels,
                                meta=json.dumps({"date": day, "item_id": item["id"], "offset": offset,
                                                 "offset_note": note, "pixel_m": PIXEL_M,
                                                 "worldcover_year": int(day[:4]),
                                                 "clear_fraction": round(float(usable.mean()), 3)}))
            tmp.replace(out)
            share = np.bincount(labels[labels < 4], minlength=4) / max((labels < 4).sum(), 1)
            print(f"  {day}: saved ({time.time() - t0:.0f} s, {usable.mean():.0%} clear, labels "
                  f"water {share[0]:.1%} veg {share[1]:.1%} bare {share[2]:.1%} built {share[3]:.1%})")
    print(f"\nTraining images in {OUT_DIR}: {len(list(OUT_DIR.glob('*.npz')))}")


if __name__ == "__main__":
    main()
