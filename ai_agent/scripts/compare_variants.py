"""Try measurement settings on the Earth Engine cross-check lake-dates.

Re-measures the lake-dates in reports/gee/gee_crosscheck.csv with the CURRENT
lakewater.py under several settings ("variants"), and compares each result
with the independent sources (radar, JRC, Dynamic World) from that file.
A variant overrides constants of lakewater.py, e.g. T_MIN=0.0.

  python ai_agent/scripts/compare_variants.py
  python ai_agent/scripts/compare_variants.py --variants "base:" "nobody:BODY_REACH_M=1" \
      "strict:T_MIN=0.0,BODY_REACH_M=1"

It only ranks settings. The sources are not ground truth, so check the
winner on pictures before adopting it.
"""

import argparse
import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
SERIES = ROOT / "reports" / "lakes" / "lake_water_timeseries.csv"
CROSS = ROOT / "reports" / "gee" / "gee_crosscheck.csv"
OUT = ROOT / "reports" / "gee" / "variants.csv"


def evaluate(records, names):
    """records: dicts with `<name>` areas and dw_ha/jrc_ha/s1_ha references."""
    from scipy.stats import spearmanr
    print(f"\n{len(records)} lake-dates")
    for ref, label in (("s1_ha", "Sentinel-1 radar"), ("jrc_ha", "JRC monthly"),
                       ("dw_ha", "Dynamic World")):
        print(f"\nagainst {label}   (lower typical error is better)")
        print(f"  {'setting':10s} {'n':>4s} {'typical error':>14s} "
              f"{'within 25%':>11s} {'rank corr':>10s} {'ours/theirs':>12s}")
        for name in names:
            pairs = [(float(r[name]), float(r[ref])) for r in records
                     if r.get(name) not in (None, "") and r.get(ref) not in (None, "")]
            big = [(a, b) for a, b in pairs if a > 0.5 or b > 0.5]
            if len(pairs) < 5 or not big:
                print(f"  {name:10s} too few")
                continue
            a = np.array(big)
            ratio = (a[:, 0] + 0.5) / (a[:, 1] + 0.5)      # +0.5 ha: damp tiny lakes
            err = float(np.median(np.abs(np.log2(ratio))))
            within = float(np.mean(np.abs(ratio - 1) <= 0.25))
            p = np.array(pairs)
            rho = (spearmanr(p[:, 0], p[:, 1])[0]
                   if p[:, 0].std() > 0 and p[:, 1].std() > 0 else float("nan"))
            print(f"  {name:10s} {len(pairs):4d} {2 ** err:13.2f}x "
                  f"{within * 100:10.0f}% {rho:10.2f} "
                  f"{float(np.median(ratio)):12.2f}")
    print("\ntypical error: 1.00x = identical, 1.50x = typically 50% apart "
          "(either direction).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="*", default=[
        "base:", "nobody:BODY_REACH_M=1", "tmin0:T_MIN=0.0",
        "nosoft:SOFT_SUPPORT_PX=1000000000", "nodark:DARK_REFL=0.0",
        "nohole:HOLE_MAX_PX=0"])
    args = ap.parse_args()
    import lakewater as lw

    with open(CROSS, newline="", encoding="utf-8") as h:
        cross = list(csv.DictReader(h))
    with open(SERIES, newline="", encoding="utf-8") as h:
        series = {(r["lake_id"], r["date"]): r for r in csv.DictReader(h)
                  if r["source"] == "archive"}
    lakes = {l["id"]: l for l in lw.load_lakes(min_ha=5.0)}
    variants = []
    for spec in args.variants:
        name, _, rest = spec.partition(":")
        sets = {}
        for part in filter(None, rest.split(",")):
            key, _, val = part.partition("=")
            sets[key.strip()] = float(val)
        variants.append((name, sets))
    names = [n for n, _ in variants]
    defaults = {k: getattr(lw, k) for _, s in variants for k in s}

    by_item = defaultdict(list)
    for r in cross:
        s = series.get((r["lake_id"], r["date"]))
        if s and r["lake_id"] in lakes:
            by_item[(r["date"][:4], s["item_id"])].append(r)
    records = {}
    for year in sorted({y for y, _ in by_item}):
        wanted = [(i, rs) for (y, i), rs in by_item.items() if y == year]
        boxes = [lakes[r["lake_id"]]["geom_ll"].bounds for _, rs in wanted for r in rs]
        bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))
        try:
            items = {i["id"]: i for i in lw.search_archive(bbox, int(year), int(year))}
        except Exception as error:
            print(f"{year}: archive search failed ({error})")
            continue
        for item_id, rs in wanted:
            item = items.get(item_id)
            scene = None
            for attempt in range(3):
                if item is None:
                    break
                try:
                    scene = lw.load_archive_scene(item, [lakes[r["lake_id"]] for r in rs])
                    break
                except Exception as error:
                    print(f"  {item_id}: read failed ({error}); retry {attempt + 1}/3")
                    time.sleep(4)
            if scene is None:
                continue
            for r in rs:
                rec = dict(r)
                for name, sets in variants:
                    for key, val in sets.items():
                        setattr(lw, key, val)
                    row, _ = lw.measure_lake(scene, lakes[r["lake_id"]])
                    for key in sets:
                        setattr(lw, key, defaults[key])
                    ok = row is not None and row["status"] in ("ok", "small") \
                        and row["water_ha"] != ""
                    rec[name] = row["water_ha"] if ok else ""
                records[(r["lake_id"], r["date"])] = rec
                print(f"  {r['lake'][:24]:24s} {r['date']}  " + "  ".join(
                    f"{n}={rec[n]}" for n in names), flush=True)
    out = list(records.values())
    if out:
        with open(OUT, "w", newline="", encoding="utf-8") as h:
            w = csv.DictWriter(h, fieldnames=list(out[0]))
            w.writeheader()
            w.writerows(out)
        print(f"\nsaved {OUT}")
    evaluate(out, names)


if __name__ == "__main__":
    main()
