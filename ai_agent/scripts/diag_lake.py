import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lakewater as lw

SCL = {0: "nodata", 1: "saturated", 2: "dark area", 3: "cloud shadow",
       4: "vegetation", 5: "bare soil", 6: "water", 7: "unclassified",
       8: "cloud medium", 9: "cloud high", 10: "thin cirrus", 11: "snow"}


def stats(label, d, mask):
    if not mask.any():
        return
    ndvi = np.where((d["nir"] + d["red"]) > 0,
                    (d["nir"] - d["red"]) / (d["nir"] + d["red"]), 0)
    print(f"  {label}: {int(mask.sum())} px")
    for name, v in (("green", d["green"]), ("nir", d["nir"]),
                    ("swir", d["swir"]), ("mndwi", d["mndwi"]), ("ndvi", ndvi)):
        x = v[mask]
        print(f"    {name:6s} p10 {np.percentile(x,10):6.3f}  median "
              f"{np.median(x):6.3f}  p90 {np.percentile(x,90):6.3f}")


def report(row, d):
    inside, water, clear = d["inside"], d["water"], d["clear"]
    left = inside & ~water
    print(f"\nrow: water {row['water_ha']} ha, threshold {row['threshold']} "
          f"({row['threshold_method']}), valid {row['valid_frac']}")
    print(f"pixels inside outline: {int(inside.sum())}, counted water: "
          f"{int((inside & water).sum())}, not water: {int(left.sum())}")
    stats("COUNTED AS WATER", d, inside & water)
    masked = left & ~clear
    print(f"  not water because MASKED: {int(masked.sum())}")
    scl = d.get("scl")
    if scl is not None and masked.any():
        vals, cnt = np.unique(scl[masked], return_counts=True)
        print("    masked pixels by scene class: " + ", ".join(
            f"{SCL.get(int(v), v)}={int(c)}" for v, c in zip(vals, cnt)))
    k = left & clear
    stats("NOT WATER although clear", d, k)
    if k.any():
        print(f"    of these, very dark in NIR (<0.06): "
              f"{int((k & (d['nir'] < 0.06)).sum())}")


def main(name, date):
    lakes = [l for l in lw.load_lakes(min_ha=5.0)
             if name == l["id"] or name.lower() in l["name"].lower()]
    if not lakes:
        raise SystemExit("lake not found")
    lake = lakes[0]
    year = int(date[:4])
    b = lake["geom_ll"].bounds
    items = [i for i in lw.search_archive(b, year, year)
             if i["properties"]["datetime"][:10] == date]
    print(f"{lake['name']}: {len(items)} scene(s) on {date}")
    for item in items:
        scene = lw.load_archive_scene(item, [lake])
        if scene is None:
            continue
        print(item["id"], "scene cloud", scene.cloud_pct)
        row, d = lw.measure_lake(scene, lake, keep_debug=True)
        if d is not None:
            report(row, d)
            if len(sys.argv) > 3:
                out = f"{sys.argv[3]}_{item['id']}.png"
                lw.save_chip(out, row, d)
                print('  picture:', out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
