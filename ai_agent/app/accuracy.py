"""Accuracy numbers for the website, all read from results already produced
by the evaluation scripts (nothing here is estimated or invented).

  * land-cover model vs ESA WorldCover: held-out metrics, confusion matrix,
    per-scene metrics, and the first (rule-label) model for comparison
    (evaluate_vs_worldcover.py / evaluate_model.py outputs)
  * per lake: model prediction vs WorldCover inside that lake's outline, on the
    two scenes that have WorldCover labels (2020, 2021)
  * lake open-water measurement vs three independent products in
    reports/gee/gee_crosscheck.csv (gee_crosscheck.py):
        dw  = Google Dynamic World (optical, deep-learning)
        jrc = JRC Global Surface Water (Landsat, monthly)
        s1  = Sentinel-1 radar (sees water through clouds and weeds differ)
"""

import csv
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import lake_analytics as la

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "reports" / "evaluation"
GEE_CSV = ROOT / "reports" / "gee" / "gee_crosscheck.csv"
PRED_DIR = ROOT / "reports" / "predictions_6band"
WC_DIR = ROOT / "data" / "worldcover"
CLASSES = ["water", "vegetation", "bare_land", "built_up"]
WC_SCENES = {"8april20": "2020-04-08", "3april21": "2021-04-03"}
REFERENCES = {"dw": "Dynamic World", "jrc": "JRC Global Surface Water",
              "s1": "Sentinel-1 radar"}
AGREE_PCT = 20.0          # "agrees" = within 20% ...
AGREE_HA = 2.0            # ... or within 2 ha (small lakes)


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_csv(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _metrics_row(row):
    out = {"accuracy": _num(row.get("accuracy")), "mean_iou": _num(row.get("mean_iou")),
           "per_class": {}}
    for c in CLASSES:
        out["per_class"][c] = {"precision": _num(row.get(f"{c}_precision")),
                               "recall": _num(row.get(f"{c}_recall"))}
    return out


def _from_matrix(cm):
    cm = np.asarray(cm, dtype=float)
    n = cm.sum()
    tp = np.diag(cm)
    with np.errstate(divide="ignore", invalid="ignore"):
        precision = np.where(cm.sum(0) > 0, tp / cm.sum(0), np.nan)
        recall = np.where(cm.sum(1) > 0, tp / cm.sum(1), np.nan)
        iou = np.where(cm.sum(0) + cm.sum(1) - tp > 0, tp / (cm.sum(0) + cm.sum(1) - tp), np.nan)
    oa = tp.sum() / n if n else None
    pe = float((cm.sum(0) * cm.sum(1)).sum()) / (n * n) if n else None
    kappa = (oa - pe) / (1 - pe) if n and pe < 1 else None

    def r(v):
        return None if v is None or np.isnan(v) else round(float(v), 4)
    return {"pixels": int(n), "accuracy": r(oa), "kappa": r(kappa),
            "mean_iou": r(np.nanmean(iou)) if np.isfinite(iou).any() else None,
            "per_class": {c: {"precision": r(precision[i]), "recall": r(recall[i]),
                              "iou": r(iou[i]), "reference_pixels": int(cm[i].sum())}
                          for i, c in enumerate(CLASSES)},
            "confusion_matrix": cm.astype(int).tolist()}


@lru_cache(maxsize=1)
def model_accuracy():
    heldout = _read_csv(EVAL_DIR / "worldcover_6band_heldout_metrics.csv")
    first = _read_csv(EVAL_DIR / "worldcover_metrics.csv")
    per_scene = _read_csv(EVAL_DIR / "per_scene_metrics.csv")
    cm_rows = _read_csv(EVAL_DIR / "validation_confusion_matrix.csv")
    return {
        "reference": "ESA WorldCover 10 m (2020 v100, 2021 v200)",
        "test_design": "Right-hand 30% of each WorldCover scene, never used in "
                       "training, with a 128 px gap to the training part.",
        "heldout": [{"scene": r["scene"], "date": WC_SCENES.get(r["scene"]), **_metrics_row(r)}
                    for r in heldout],
        "first_model_rule_labels": [{"scene": r["scene"], "source": r["source"],
                                     **_metrics_row(r)} for r in first],
        "validation_scene_14oct26": (
            _from_matrix([[int(v) for v in row.values()] for row in cm_rows]) if cm_rows else None),
        "per_scene": [{"scene": r["scene"], "split": r["split"], **_metrics_row(r)}
                      for r in per_scene],
        "v1_vs_v2": [{"model": r["model"], "test": r["test"], "pixels": int(r["pixels"]),
                      **{k: _num(r[k]) for k in r if k not in ("model", "test", "pixels")}}
                     for r in _read_csv(EVAL_DIR / "landcover_v2_comparison.csv")],
        "caveat": "WorldCover is itself a model (about 75% accurate), so these "
                  "numbers measure agreement with it, not field truth. Only 2020 "
                  "and 2021 have WorldCover labels.",
    }


def lake_model_accuracy(lake):
    """Model vs WorldCover inside this lake's outline, for 2020 and 2021."""
    import rasterio
    from rasterio import features
    from rasterio.errors import WindowError
    from rasterio.warp import transform_bounds, transform_geom
    from rasterio.windows import Window, from_bounds
    from shapely.geometry import mapping
    out = []
    for scene, day in WC_SCENES.items():
        pred_path = PRED_DIR / f"{scene}_landcover.tif"
        ref_path = WC_DIR / f"reference_{scene}_worldcover.tif"
        if not (pred_path.exists() and ref_path.exists()):
            continue
        with rasterio.open(pred_path) as p, rasterio.open(ref_path) as r:
            if (p.crs, p.transform, p.shape) != (r.crs, r.transform, r.shape):
                continue
            geom = transform_geom("EPSG:4326", p.crs, mapping(lake["geom"].buffer(0)))
            win = from_bounds(*transform_bounds("EPSG:4326", p.crs, *lake["geom"].bounds),
                              transform=p.transform)
            try:
                win = win.round_offsets(op="floor").round_lengths(op="ceil").intersection(
                    Window(0, 0, p.width, p.height))
            except WindowError:
                continue                    # lake outside this scene
            pred, ref = p.read(1, window=win), r.read(1, window=win)
            inside = features.rasterize([(geom, 1)], out_shape=pred.shape,
                                        transform=p.window_transform(win), fill=0,
                                        all_touched=True, dtype="uint8").astype(bool)
        keep = inside & (pred < 4) & (ref < 4)
        if keep.sum() < 20:
            continue
        cm = np.bincount(ref[keep].astype(int) * 4 + pred[keep], minlength=16).reshape(4, 4)
        out.append({"scene": scene, "date": day, **_from_matrix(cm)})
    return out


@lru_cache(maxsize=1)
def _gee_rows():
    rows = []
    for r in _read_csv(GEE_CSV):
        ours = _num(r.get("ours_ha"))
        if ours is None:
            continue
        rows.append({"lake_id": r["lake_id"], "lake": r["lake"], "date": r["date"],
                     "ours_ha": ours, **{k: _num(r.get(f"{k}_ha")) for k in REFERENCES}})
    return rows


def _agreement(pairs):
    """pairs: [(ours, theirs)] -> agreement statistics."""
    if not pairs:
        return None
    a = np.array(pairs, dtype=float)
    ours, ref = a[:, 0], a[:, 1]
    diff = ours - ref
    close = np.abs(diff) <= np.maximum(AGREE_HA, AGREE_PCT / 100 * np.maximum(ours, ref))
    r = float(np.corrcoef(ours, ref)[0, 1]) if len(a) > 2 and ours.std() and ref.std() else None
    return {"n": int(len(a)), "pearson_r": None if r is None else round(r, 3),
            "mean_abs_diff_ha": round(float(np.abs(diff).mean()), 2),
            "median_abs_diff_ha": round(float(np.median(np.abs(diff))), 2),
            "bias_ha": round(float(diff.mean()), 2),
            "agree_pct": round(float(close.mean() * 100), 1)}


def measurement_accuracy(lake_id=None):
    rows = [r for r in _gee_rows() if lake_id is None or r["lake_id"] == lake_id]
    out = {"references": REFERENCES,
           "agreement_rule": f"within {AGREE_PCT:.0f}% or {AGREE_HA:.0f} ha",
           "lakes": len({r["lake_id"] for r in rows}), "comparisons": len(rows),
           "by_reference": {k: _agreement([(r["ours_ha"], r[k]) for r in rows
                                           if r[k] is not None]) for k in REFERENCES},
           "note": "None of these is ground truth: Dynamic World and JRC are other "
                   "satellite products, radar reads floating weeds differently. "
                   "Agreement with several independent sources is the evidence."}
    if lake_id is not None:
        out["points"] = [{k: r[k] for k in ("date", "ours_ha", *REFERENCES)} for r in rows]
    return out


def accuracy(lake=None):
    out = {"model": model_accuracy(), "measurement": measurement_accuracy(
        lake["id"] if lake else None)}
    if lake is not None:
        out["lake_id"], out["lake"] = lake["id"], lake["name"]
        out["lake_model_vs_worldcover"] = lake_model_accuracy(lake)
        out["measurement_overall"] = measurement_accuracy(None)["by_reference"]
    return out
