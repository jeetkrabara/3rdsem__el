"""Explain what the land-cover model relies on, for one lake.

All methods run on the same crop of the same real Sentinel-2 scene and target
the LAKE OUTLINE (OpenStreetMap polygon) - not the rectangle around it, which
for most lakes is mostly roads and buildings.

  * OCCLUSION (fast): each of the 9 inputs is replaced by its typical value in
    the crop and we count how many predicted pixels inside the lake change.
  * GRAD-CAM: where in the crop the model's evidence for a class (water by
    default) inside the lake comes from (gradient of the class score summed
    over the lake, read at an inner U-Net layer).
  * SHAP (slower): GradientSHAP. Each input gets a signed contribution, in
    probability points, to the lake's mean class probability. Contributions
    are SUMMED over pixels so they add up (approximately) to
    "prediction - background prediction"; that check is returned.

Limits: these explain the MODEL, not the ground. Inputs are correlated (NDWI
is built from green and NIR), so credit can be shared or hidden. Grad-CAM is
coarse (the layer is half the image resolution). SHAP is sampled, so values
vary slightly between runs. Needs PyTorch, the trained model and the scene
files on this machine (SHAP also needs `pip install shap`).
"""

import base64
import io
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CHANNEL_NAMES = ["B02 blue", "B03 green", "B04 red", "B08 near-infrared",
                 "B11 shortwave-infrared 1", "B12 shortwave-infrared 2",
                 "NDVI (greenness)", "NDWI (water index)",
                 "NDBI (built-up index)"]
CLASS_COLORS = {0: (0, 100, 200), 1: (40, 160, 60), 2: (200, 170, 100),
                3: (220, 60, 60)}
MAX_WINDOW = 320
PAD = 32
MAX_GAP_DAYS = 45
CAM_LAYER = "dec2"          # decoder block at half resolution
SHAP_MAX_CROP = 160
SHAP_SAMPLES = 64
SHAP_BACKGROUND = 8


class XaiUnavailable(Exception):
    pass


def _lc():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    try:
        import landcover6
    except ModuleNotFoundError as error:
        missing = getattr(error, "name", "") or str(error)
        if missing == "landcover6":
            raise XaiUnavailable("ai_agent/scripts/landcover6.py not found.")
        raise XaiUnavailable(f"Missing package: {missing}. "
                             "Install it in the AI service environment.")
    return landcover6


@lru_cache(maxsize=1)
def _model():
    lc = _lc()
    if not lc.MODEL_PATH.exists():
        raise XaiUnavailable("Trained model file not found "
                             f"({lc.MODEL_PATH}). Run "
                             "train_worldcover_6band.py.")
    return lc.load_model()


@lru_cache(maxsize=2)
def _scene(name):
    lc = _lc()
    features, valid, profile, reason = lc.load_scene(lc.DATA_DIR / name)
    if features is None:
        raise XaiUnavailable(f"Scene {name}: {reason}")
    return features, valid, profile


def _scene_choices():
    lc = _lc()
    choices = []
    if not lc.DATA_DIR.exists():
        return choices
    for folder in lc.DATA_DIR.iterdir():
        if not folder.is_dir():
            continue
        y, m, d = lc.scene_date_key(folder.name)
        if y == 9999 or m == 0:
            continue
        if all(lc.find_band(folder, b) is not None for b in lc.BANDS):
            choices.append((date(y, m, d), folder.name))
    return sorted(choices)


def available_scenes():
    """Scenes the model can be explained on: [{scene, date}]."""
    return [{"scene": name, "date": d.isoformat()} for d, name in _scene_choices()]


def _png(arr):
    from PIL import Image
    buffer = io.BytesIO()
    Image.fromarray(arr).save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _edge(mask):
    from scipy import ndimage
    return mask & ~ndimage.binary_erosion(mask)


def _false_colour(feat):
    """NIR, red, green as R, G, B (plants red, water dark), 0-1."""
    rgb = np.stack([feat[3], feat[2], feat[1]], axis=-1)
    return np.clip(rgb * 1.6, 0, 1)


def _with_outline(rgb_uint8, inside):
    out = rgb_uint8.copy()
    out[_edge(inside)] = (255, 225, 60)
    return out


def prepare(bbox, when=None, lake_geom=None, scene_name=None):
    """Crop the scene around the lake; everything the methods share.

    lake_geom: shapely polygon in lon/lat. When given, the explained area is
    that outline; otherwise the bbox rectangle."""
    from rasterio import features as rfeatures
    from rasterio.warp import transform_bounds, transform_geom
    from rasterio.windows import from_bounds
    from shapely.geometry import mapping

    lc = _lc()
    choices = _scene_choices()
    if not choices:
        raise XaiUnavailable("No scene folders with all six bands found.")
    if scene_name:
        named = [c for c in choices if c[1] == scene_name]
        if not named:
            raise XaiUnavailable(f"Unknown scene '{scene_name}'.")
        chosen = named[0]
    else:
        chosen = choices[-1] if when is None else min(
            choices, key=lambda c: abs((c[0] - when).days))
        if when is not None and abs((chosen[0] - when).days) > MAX_GAP_DAYS:
            raise XaiUnavailable(
                f"No scene within {MAX_GAP_DAYS} days of {when} "
                f"(nearest is {chosen[0]}).")
    features, valid, profile = _scene(chosen[1])
    model = _model()

    target_bounds = lake_geom.bounds if lake_geom is not None else bbox
    _, height, width = features.shape
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", profile["crs"], *target_bounds)
    win = from_bounds(left, bottom, right, top, profile["transform"])
    col0, row0 = int(win.col_off), int(win.row_off)
    col1, row1 = int(win.col_off + win.width), int(win.row_off + win.height)
    col0, row0 = max(col0, 0), max(row0, 0)
    col1, row1 = min(col1, width), min(row1, height)
    if col1 - col0 < 4 or row1 - row0 < 4:
        raise XaiUnavailable("This lake is outside the scene.")
    c0, r0 = max(col0 - PAD, 0), max(row0 - PAD, 0)
    c1, r1 = min(col1 + PAD, width), min(row1 + PAD, height)
    if c1 - c0 > MAX_WINDOW:
        mid = (col0 + col1) // 2
        c0, c1 = max(mid - MAX_WINDOW // 2, 0), min(mid + MAX_WINDOW // 2, width)
    if r1 - r0 > MAX_WINDOW:
        mid = (row0 + row1) // 2
        r0, r1 = max(mid - MAX_WINDOW // 2, 0), min(mid + MAX_WINDOW // 2, height)

    feat = features[:, r0:r1, c0:c1]
    ok = valid[r0:r1, c0:c1]
    crop_transform = profile["transform"] * profile["transform"].translation(c0, r0)
    if lake_geom is not None:
        geom = transform_geom("EPSG:4326", profile["crs"], mapping(lake_geom))
        inside = rfeatures.rasterize([(geom, 1)], out_shape=ok.shape,
                                     transform=crop_transform, fill=0,
                                     dtype="uint8", all_touched=True).astype(bool)
        region = "lake outline (OpenStreetMap)"
    else:
        inside = np.zeros(ok.shape, dtype=bool)
        inside[max(row0 - r0, 0):row1 - r0, max(col0 - c0, 0):col1 - c0] = True
        region = "bounding box"
    inside &= ok
    count = int(inside.sum())
    if count == 0:
        raise XaiUnavailable("No valid pixels over this lake in that scene.")

    # lon/lat bounds of the crop, for drawing it on a map
    tr = profile["transform"]
    west, north = tr * (c0, r0)
    east, south = tr * (c1, r1)
    w84, s84, e84, n84 = transform_bounds(
        profile["crs"], "EPSG:4326", west, south, east, north)
    return {"lc": lc, "model": model, "scene": chosen, "feat": feat,
            "ok": ok, "inside": inside, "count": count, "region": region,
            "pixel_m": round(abs(tr.a), 1),
            "image_bounds": [round(w84, 6), round(s84, 6),
                             round(e84, 6), round(n84, 6)],
            "all_features": features, "all_valid": valid,
            "origin": (r0, c0)}


def _class_index(lc, class_name):
    if class_name not in lc.CLASS_NAMES:
        raise XaiUnavailable(f"Unknown class '{class_name}'. "
                             f"Use one of {', '.join(lc.CLASS_NAMES)}.")
    return lc.CLASS_NAMES.index(class_name)


# --------------------------------------------------------------- prediction
def prediction(prep):
    """What the model predicts for the crop (the thing being explained)."""
    lc, model = prep["lc"], prep["model"]
    feat, ok, inside, count = (prep["feat"], prep["ok"], prep["inside"],
                               prep["count"])
    labels = lc.predict_scene(model, feat, ok)
    share = np.bincount(labels[inside], minlength=256)[:4] / count * 100
    rgb = np.zeros(labels.shape + (3,), dtype=np.uint8)
    for k, c in CLASS_COLORS.items():
        rgb[labels == k] = c
    rgb[~ok] = (128, 128, 128)
    fc = (_false_colour(feat) * 255).astype(np.uint8)
    return {
        "scene": prep["scene"][1], "scene_date": prep["scene"][0].isoformat(),
        "region": prep["region"], "pixels_in_area": count,
        "pixel_m": prep["pixel_m"],
        "predicted_shares_pct": {cls: round(float(share[k]), 1)
                                 for k, cls in enumerate(lc.CLASS_NAMES)},
        "class_colors": {cls: "#%02x%02x%02x" % CLASS_COLORS[k]
                         for k, cls in enumerate(lc.CLASS_NAMES)},
        "false_colour_png_base64": _png(_with_outline(fc, inside)),
        "prediction_png_base64": _png(_with_outline(rgb, inside)),
        "image_bounds": prep["image_bounds"],
    }, labels


# ------------------------------------------------------------------ occlusion
def occlusion(prep, base=None):
    lc, model = prep["lc"], prep["model"]
    feat, ok, inside, count = (prep["feat"], prep["ok"], prep["inside"],
                               prep["count"])
    if base is None:
        base = lc.predict_scene(model, feat, ok)

    def shares(labels):
        return np.bincount(labels[inside], minlength=256)[:4] / count * 100

    base_share = shares(base)
    rows = []
    for index, name in enumerate(CHANNEL_NAMES):
        changed = feat.copy()
        changed[index][ok] = float(np.median(feat[index][ok]))
        pred = lc.predict_scene(model, changed, ok)
        new_share = shares(pred)
        rows.append({
            "input": name,
            "pixels_changed_pct": round(float((pred[inside] != base[inside]).mean() * 100), 1),
            "share_change_pts": {
                cls: round(float(new_share[k] - base_share[k]), 1)
                for k, cls in enumerate(lc.CLASS_NAMES)},
        })
    rows.sort(key=lambda r: -r["pixels_changed_pct"])
    top = rows[:3]
    text = (f"Inside the lake outline on {prep['scene'][0].isoformat()}, the model leans most on "
            + ", ".join(f"{r['input']} ({r['pixels_changed_pct']}% of pixels "
                        "change class if it is removed)" for r in top)
            + ". Occlusion explains the model, not the ground; correlated "
              "inputs can share or hide importance.")
    return {
        "method": "occlusion: each input replaced by its median in the window",
        "scene": prep["scene"][1], "scene_date": prep["scene"][0].isoformat(),
        "region": prep["region"], "pixels_in_area": count,
        "predicted_shares_pct": {cls: round(float(base_share[k]), 1)
                                 for k, cls in enumerate(lc.CLASS_NAMES)},
        "inputs": rows, "explanation": text,
    }


# ------------------------------------------------------------------- Grad-CAM
def gradcam(prep, class_name="water"):
    import torch
    import torch.nn.functional as F
    from matplotlib import colormaps
    lc, model = prep["lc"], prep["model"]
    k = _class_index(lc, class_name)
    feat, ok, inside = prep["feat"], prep["ok"], prep["inside"]
    height, width = ok.shape
    ph, pw = -height % 4, -width % 4
    x = np.pad(feat, ((0, 0), (0, ph), (0, pw)), mode="reflect")
    mask = np.pad(inside, ((0, ph), (0, pw)), mode="constant")
    layer = getattr(model, CAM_LAYER)

    store = {}

    def forward_hook(_, __, output):
        store["act"] = output
        output.register_hook(lambda grad: store.__setitem__("grad", grad))

    handle = layer.register_forward_hook(forward_hook)
    try:
        with torch.enable_grad():
            tensor = torch.from_numpy(x).unsqueeze(0).float()
            logits = model(tensor)
            score = logits[0, k][torch.from_numpy(mask)].sum()
            model.zero_grad()
            score.backward()
    finally:
        handle.remove()
    act, grad = store["act"][0].detach(), store["grad"][0].detach()
    weights = grad.mean(dim=(1, 2), keepdim=True)
    cam = torch.relu((weights * act).sum(0, keepdim=True)).unsqueeze(0)
    cam = F.interpolate(cam, size=x.shape[1:], mode="bilinear",
                        align_corners=False)[0, 0].numpy()
    cam = cam[:height, :width]
    cam[~ok] = 0
    peak = float(cam.max())
    cam = cam / peak if peak > 0 else cam

    inside_mean = float(cam[inside].mean())
    outside = ok & ~inside
    outside_mean = float(cam[outside].mean()) if outside.any() else None

    # false colour underneath, CAM on top (transparent where weak), outline
    base = _false_colour(feat)
    heat = colormaps["magma"](cam)[..., :3]
    alpha = np.where(cam >= 0.05, 0.65, 0.0)[..., None]
    blended = ((base * (1 - alpha) + heat * alpha) * 255).astype(np.uint8)
    ratio = (inside_mean / outside_mean) if outside_mean else None

    return {
        "method": f"Grad-CAM on layer {CAM_LAYER}, class {class_name}",
        "class": class_name, "scene": prep["scene"][1],
        "scene_date": prep["scene"][0].isoformat(), "region": prep["region"],
        "png_base64": _png(_with_outline(blended, inside)),
        "image_bounds": prep["image_bounds"],
        "attention_inside_area": round(inside_mean, 3),
        "attention_outside_area": None if outside_mean is None else round(outside_mean, 3),
        "inside_to_outside_ratio": None if ratio is None else round(ratio, 2),
        "explanation": (
            f"Bright areas are where the model's {class_name} evidence for the "
            "lake comes from (false colour underneath: plants red, water dark; "
            "yellow line = lake outline). "
            + (f"Attention is {ratio:.1f}x stronger inside the outline than around it. "
               if ratio and ratio >= 1 else
               f"Attention is weaker inside the outline than around it ({ratio:.2f}x): the "
               f"model finds little {class_name} evidence in the lake on this date. "
               if ratio else "")
            + "Grad-CAM is coarse because it reads an inner layer at half resolution."),
    }


# ----------------------------------------------------------------------- SHAP
def shap_bands(prep, class_name="water"):
    try:
        import shap
        import torch
    except ModuleNotFoundError as error:
        raise XaiUnavailable(f"SHAP needs `pip install shap` ({error.name}).")
    from matplotlib import colormaps
    lc, model = prep["lc"], prep["model"]
    k = _class_index(lc, class_name)
    feat, ok, inside = prep["feat"], prep["ok"], prep["inside"]

    # crop that covers as much of the lake as the budget allows (multiple of 4)
    ys, xs = np.where(inside)
    extent = max(ys.max() - ys.min(), xs.max() - xs.min()) + 16
    size = int(min(SHAP_MAX_CROP, extent, feat.shape[1], feat.shape[2])) // 4 * 4
    size = max(size, 16)
    cy, cx = int((ys.min() + ys.max()) / 2), int((xs.min() + xs.max()) / 2)
    y0 = int(np.clip(cy - size // 2, 0, feat.shape[1] - size))
    x0 = int(np.clip(cx - size // 2, 0, feat.shape[2] - size))
    crop = np.ascontiguousarray(feat[:, y0:y0 + size, x0:x0 + size])
    area = inside[y0:y0 + size, x0:x0 + size]
    if not area.any():
        raise XaiUnavailable("The lake is outside the SHAP crop.")
    coverage = float(area.sum() / inside.sum())
    mask = torch.from_numpy(area)

    class ClassProbability(torch.nn.Module):
        def __init__(self, net):
            super().__init__()
            self.net = net

        def forward(self, x):
            prob = torch.softmax(self.net(x), dim=1)[:, k]
            return prob[:, mask].mean(dim=1, keepdim=True)

    # background = typical scene patches (what "no specific evidence" looks like)
    rng = np.random.default_rng(0)
    all_feat, all_valid = prep["all_features"], prep["all_valid"]
    background, tries = [], 0
    while len(background) < SHAP_BACKGROUND and tries < 400:
        tries += 1
        by = int(rng.integers(0, all_feat.shape[1] - size))
        bx = int(rng.integers(0, all_feat.shape[2] - size))
        if all_valid[by:by + size, bx:bx + size].mean() > 0.9:
            background.append(all_feat[:, by:by + size, bx:bx + size])
    if not background:
        raise XaiUnavailable("Could not find clear background patches in the scene.")
    background = torch.from_numpy(np.stack(background).astype(np.float32))

    wrapped = ClassProbability(model).eval()
    explainer = shap.GradientExplainer(wrapped, background)
    values = explainer.shap_values(torch.from_numpy(crop[None].astype(np.float32)),
                                   nsamples=SHAP_SAMPLES, rseed=0)
    if isinstance(values, list):
        values = values[0]
    values = np.asarray(values)
    if values.ndim == 5 and values.shape[-1] == 1:      # (batch, C, H, W, out)
        values = values[..., 0]
    values = values[0]                                  # -> (C, H, W)

    with torch.no_grad():
        prob = float(wrapped(torch.from_numpy(crop[None])).item())
        base_prob = float(wrapped(background).mean().item())
    # Every pixel of every input contributes; the per-input total is the SUM.
    totals = values.reshape(values.shape[0], -1).sum(axis=1)
    rows = []
    for index, name in enumerate(CHANNEL_NAMES):
        rows.append({"input": name,
                     "contribution_pts": round(float(totals[index]) * 100, 2),
                     "abs_contribution_pts": round(abs(float(totals[index])) * 100, 2)})
    total_abs = sum(r["abs_contribution_pts"] for r in rows) or 1.0
    for r in rows:
        r["share_of_total_pct"] = round(r["abs_contribution_pts"] / total_abs * 100, 1)
    rows.sort(key=lambda r: -r["abs_contribution_pts"])

    # where in the crop the evidence sits: sum over inputs, signed
    pixel = values.sum(axis=0)
    lim = float(np.percentile(np.abs(pixel), 99)) or 1e-9
    norm = np.clip(pixel / lim, -1, 1) / 2 + 0.5
    heat = (colormaps["RdBu"](norm)[..., :3] * 255).astype(np.uint8)
    heat[~ok[y0:y0 + size, x0:x0 + size]] = (128, 128, 128)

    explained = float(totals.sum())
    gap = prob - base_prob
    up = [r for r in rows if r["contribution_pts"] > 0][:2]
    down = [r for r in rows if r["contribution_pts"] < 0][:2]
    return {
        "method": f"GradientSHAP on a {size}x{size} px crop, class {class_name}",
        "class": class_name, "scene": prep["scene"][1],
        "scene_date": prep["scene"][0].isoformat(), "region": prep["region"],
        "lake_pixels_covered_pct": round(coverage * 100, 1),
        "predicted_probability_in_area": round(prob, 3),
        "background_probability": round(base_prob, 3),
        "sum_of_contributions": round(explained, 3),
        "completeness_gap": round(abs(explained - gap), 3),
        "inputs": rows,
        "map_png_base64": _png(_with_outline(heat, area)),
        "explanation": (
            f"The model gives the lake a mean {class_name} probability of {prob:.0%} "
            f"versus {base_prob:.0%} for typical scene patches. "
            + (f"Raised most by {', '.join(r['input'] for r in up)}. " if up else "")
            + (f"Lowered most by {', '.join(r['input'] for r in down)}. " if down else "")
            + "Contributions are in probability points and add up to the difference "
              f"(check: {explained * 100:+.1f} vs {gap * 100:+.1f} pts). Map: blue raises, "
              "red lowers the probability. Sampled, so values vary slightly."),
    }


def explain(bbox, when=None, methods=("occlusion", "gradcam"), lake_geom=None,
            scene_name=None, class_name="water"):
    """Run the chosen methods; one failure never hides the others."""
    prep = prepare(bbox, when, lake_geom, scene_name)
    out = {"scene": prep["scene"][1],
           "scene_date": prep["scene"][0].isoformat(),
           "region": prep["region"], "class": class_name}
    pred, labels = prediction(prep)
    out["prediction"] = pred
    runners = {"occlusion": lambda p: occlusion(p, labels),
               "gradcam": lambda p: gradcam(p, class_name),
               "shap": lambda p: shap_bands(p, class_name)}
    for name in methods:
        if name not in runners:
            continue
        try:
            out[name] = runners[name](prep)
        except XaiUnavailable as error:
            out[name] = {"unavailable": str(error)}
    return out
