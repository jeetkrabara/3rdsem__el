"""Train the land-cover U-Net v2 on many seasons, and compare it fairly with v1.

Data
  * the two downloaded April scenes with WorldCover labels (16.8 m), as before
  * the multi-season 10 m archive images from build_archive_training.py
Honest tests (nothing below is ever trained on)
  * NEW PLACES : the right 30% of the area in every image (128 px gap)
  * NEW DATES  : every 4th archive date is held out completely; scored on its
                 right 30% only, so it is both a new date AND a new place
The current model (v1) is scored on exactly the same pixels, so the table at
the end is a like-for-like comparison. The new model is saved separately
(models/landcover_6band_unet_v2.pth); use --install to make it the one the
website uses (the old file is kept as landcover_6band_unet_v1.pth).

Run from the project root:
    python ai_agent/scripts/train_landcover_v2.py              # train + compare
    python ai_agent/scripts/train_landcover_v2.py --install    # also switch the site to v2
Options: --steps 4000 --batch 16 --crop 128 --base 32 --max-weight 3
"""

import argparse
import csv
import json
import random
import shutil
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

from landcover6 import (CLASS_NAMES, DATA_DIR, IGNORE, IN_CHANNELS, MODEL_PATH, NUM_CLASSES, ROOT,
                        SCALING_VERSION, WC_DIR, UNet, confusion, load_model, load_scene,
                        metrics_from)

SEED = 42
TEST_FRACTION = 0.30
GAP = 128
HOLD_OUT_EVERY = 4
ARCHIVE_DIR = ROOT / "data" / "training_archive"
V2_PATH = ROOT / "models" / "landcover_6band_unet_v2.pth"
V1_BACKUP = ROOT / "models" / "landcover_6band_unet_v1.pth"
REPORT = ROOT / "reports" / "evaluation" / "landcover_v2_comparison.csv"


# ------------------------------------------------------------------ data
def load_local():
    out = {}
    for path in sorted(WC_DIR.glob("reference_*_worldcover.tif")):
        import rasterio
        name = path.name[len("reference_"):-len("_worldcover.tif")]
        features, valid, _, reason = load_scene(DATA_DIR / name)
        if features is None:
            print(f"SKIP {name}: {reason}")
            continue
        with rasterio.open(path) as src:
            labels = src.read(1).copy()
        labels[~valid] = IGNORE
        out[f"local_{name}"] = {"features": features.astype(np.float16), "labels": labels,
                                "kind": "local 16.8 m", "date": name}
    return out


def load_archive():
    out = {}
    for path in sorted(ARCHIVE_DIR.glob("*.npz")):
        try:
            z = np.load(path)
            meta = json.loads(str(z["meta"]))
        except Exception:                       # noqa: BLE001 - still being written
            print(f"SKIP {path.name}: unfinished or unreadable")
            continue
        out[f"archive_{meta['date']}"] = {"features": z["features"], "labels": z["labels"],
                                          "kind": "archive 10 m", "date": meta["date"]}
    return out


def split(scenes):
    """Mark the training columns and which archive dates are fully held out."""
    archive = sorted(k for k in scenes if k.startswith("archive_"))
    held = set(archive[HOLD_OUT_EVERY - 1::HOLD_OUT_EVERY])
    for key, s in scenes.items():
        width = s["labels"].shape[1]
        s["test_start"] = int(width * (1 - TEST_FRACTION))
        s["train_end"] = 0 if key in held else s["test_start"] - GAP
        s["held_out_date"] = key in held
    return held


def class_weights(scenes, cap):
    counts = np.zeros(NUM_CLASSES)
    for s in scenes.values():
        region = s["labels"][:, :s["train_end"]]
        counts += np.bincount(region[region < NUM_CLASSES], minlength=NUM_CLASSES)
    freq = counts / counts.sum()
    w = np.clip((1 / np.sqrt(freq + 1e-6)) / (1 / np.sqrt(freq + 1e-6)).min(), 1, cap)
    print("Training label shares: " + ", ".join(f"{n} {f:.1%}" for n, f in zip(CLASS_NAMES, freq)))
    print("Class weights: " + ", ".join(f"{n} {v:.2f}" for n, v in zip(CLASS_NAMES, w)))
    return torch.tensor(w, dtype=torch.float32)


def batch(scenes, size, crop, rng):
    keys = [k for k, s in scenes.items() if s["train_end"] > crop]
    xs, ys = [], []
    while len(xs) < size:
        s = scenes[rng.choice(keys)]
        h = s["labels"].shape[0]
        for _ in range(20):
            y, x = rng.randint(0, h - crop), rng.randint(0, s["train_end"] - crop)
            lab = s["labels"][y:y + crop, x:x + crop]
            if (lab < NUM_CLASSES).mean() >= 0.5:
                break
        feat = s["features"][:, y:y + crop, x:x + crop].astype(np.float32)
        k = rng.randint(0, 3)
        feat, lab = np.rot90(feat, k, (1, 2)), np.rot90(lab, k)
        if rng.random() < 0.5:
            feat, lab = feat[:, :, ::-1], lab[:, ::-1]
        # season / haze robustness: small brightness and offset jitter on the six bands
        feat = feat.copy()
        feat[:6] = np.clip(feat[:6] * rng.uniform(0.9, 1.1) + rng.uniform(-0.02, 0.02), 0, 1)
        xs.append(np.ascontiguousarray(feat))
        ys.append(np.ascontiguousarray(lab))
    return torch.from_numpy(np.stack(xs)), torch.from_numpy(np.stack(ys).astype(np.int64))


# ------------------------------------------------------------------ evaluation
@torch.inference_mode()
def predict(model, feat, device, core=512, margin=32):
    _, h, w = feat.shape
    ph, pw = -h % core, -w % core
    padded = np.pad(feat, ((0, 0), (margin, margin + ph), (margin, margin + pw)), mode="reflect")
    out = np.zeros((h + ph, w + pw), dtype=np.uint8)
    for y in range(0, h, core):
        for x in range(0, w, core):
            win = padded[:, y:y + core + 2 * margin, x:x + core + 2 * margin]
            logits = model(torch.from_numpy(np.ascontiguousarray(win)).unsqueeze(0).to(device))
            out[y:y + core, x:x + core] = logits.argmax(1)[0].cpu().numpy()[margin:-margin, margin:-margin]
    return out[:h, :w]


def evaluate(model, scenes, device):
    """Confusion matrices on the right 30% strip, grouped by test set."""
    groups = {"new places (training dates)": np.zeros((4, 4), int),
              "new dates + new places": np.zeros((4, 4), int)}
    per_scene = {}
    model.eval()
    for key, s in scenes.items():
        x0 = s["test_start"]
        feat = s["features"][:, :, x0:].astype(np.float32)
        lab = s["labels"][:, x0:]
        pred = predict(model, feat, device)
        pred[lab == IGNORE] = IGNORE
        cm = confusion(lab, pred)
        per_scene[key] = cm
        groups["new dates + new places" if s["held_out_date"] else "new places (training dates)"] += cm
    return groups, per_scene


def summary(cm):
    precision, recall, iou, acc, miou = metrics_from(cm)
    n = cm.sum()
    pe = float((cm.sum(0) * cm.sum(1)).sum()) / (n * n) if n else 0
    kappa = (acc - pe) / (1 - pe) if n else float("nan")
    return {"accuracy": acc, "kappa": kappa, "mean_iou": miou,
            **{f"{c}_precision": p for c, p in zip(CLASS_NAMES, precision)},
            **{f"{c}_recall": r for c, r in zip(CLASS_NAMES, recall)}}


# ------------------------------------------------------------------ main
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=4000)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--crop", type=int, default=128)
    parser.add_argument("--base", type=int, default=32)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--max-weight", type=float, default=3.0)
    parser.add_argument("--install", action="store_true", help="make v2 the model the site uses")
    args = parser.parse_args()
    random.seed(SEED); np.random.seed(SEED); torch.manual_seed(SEED)
    device = "mps" if torch.backends.mps.is_available() else "cpu"

    scenes = {**load_local(), **load_archive()}
    held = split(scenes)
    if not any(k.startswith("archive_") for k in scenes):
        raise SystemExit("No archive training images. Run build_archive_training.py first.")
    print(f"{len(scenes)} images ({sum(k.startswith('archive_') for k in scenes)} archive); "
          f"dates held out completely: {', '.join(scenes[k]['date'] for k in sorted(held)) or 'none'}")

    model = UNet(in_channels=IN_CHANNELS, base=args.base).to(device)
    weights = class_weights(scenes, args.max_weight).to(device)
    loss_fn = nn.CrossEntropyLoss(weight=weights, ignore_index=IGNORE)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=args.steps)
    rng = random.Random(SEED)
    t0, running = time.time(), 0.0
    for step in range(1, args.steps + 1):
        model.train()
        x, y = batch(scenes, args.batch, args.crop, rng)
        loss = loss_fn(model(x.to(device)), y.to(device))
        opt.zero_grad(); loss.backward(); opt.step(); sched.step()
        running += loss.item()
        if step % 200 == 0:
            print(f"  step {step}/{args.steps}  loss {running / 200:.3f}  ({time.time() - t0:.0f} s)", flush=True)
            running = 0.0

    model_cpu = model.to("cpu")
    torch.save({"model_state_dict": model_cpu.state_dict(), "in_channels": IN_CHANNELS, "base": args.base,
                "classes": dict(enumerate(CLASS_NAMES)), "scaling": SCALING_VERSION,
                "trained_on": sorted(s["date"] for k, s in scenes.items() if not s["held_out_date"]),
                "held_out_dates": sorted(scenes[k]["date"] for k in held),
                "note": "v2: multi-season 10 m archive + local scenes, WorldCover labels, right 30% held out"},
               V2_PATH)
    print(f"\nSaved {V2_PATH.name}. Scoring v1 and v2 on the same held-out pixels...")

    v1 = load_model(MODEL_PATH).to(device)
    v2 = model_cpu.to(device)
    rows = []
    for name, m in (("v1 (current)", v1), ("v2 (new)", v2)):
        groups, per_scene = evaluate(m, scenes, device)
        for group, cm in groups.items():
            if cm.sum():
                rows.append({"model": name, "test": group, "pixels": int(cm.sum()), **summary(cm)})
    print(f"\n{'model':14} {'test':30} {'acc':>6} {'kappa':>6} {'mIoU':>6} {'water P/R':>11} {'bare P/R':>11}")
    for r in rows:
        print(f"{r['model']:14} {r['test']:30} {r['accuracy']:6.1%} {r['kappa']:6.2f} {r['mean_iou']:6.1%} "
              f"{r['water_precision']:5.0%}/{r['water_recall']:4.0%} {r['bare_land_precision']:5.0%}/{r['bare_land_recall']:4.0%}")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()} for r in rows)
    print(f"Comparison saved to {REPORT}")

    if args.install:
        if not V1_BACKUP.exists():
            shutil.copy(MODEL_PATH, V1_BACKUP)
        shutil.copy(V2_PATH, MODEL_PATH)
        print(f"Installed v2 as {MODEL_PATH.name} (v1 kept as {V1_BACKUP.name}).")


if __name__ == "__main__":
    main()
