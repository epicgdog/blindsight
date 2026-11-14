"""Part 3 synthetic data: replaces the photo walk (the human no longer walks the lobby).

Positives: views from the locked pano at random yaw (uniform over 360), pitch, roll,
with 2% scale and small translation jitter, plus brightness, contrast, gamma, blur,
noise and JPEG artifacts. Hard negatives: views from the OTHER panoLocations of the
same model (same lobby), drawn the same way, weighted toward the nearest scan
points, at least 60 of them. Every frame is split 75% calib / 25% test with a fixed
seed. Walkthrough sequences (20 positive pans, 20 negative pans) are test-only.

Some negative scan points are marked "holdout": eval_localization.py's stress test
runs with them removed from the competing places, which measures how the locked
pano behaves at spots no competing view covers (closest available stand-in for
"standing between scan points"). Everything here is SIMULATED and must be reported
as simulated.

  python tools/make_synthetic.py                      # data/synthetic/manifest.json
  python tools/make_synthetic.py --n-pos 600 --n-neg 600 --seed 1
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))
from refs import DATA_DIR, CubeSampler, load_faces, load_layout, load_pano_meta  # noqa: E402
from synthetic_feed import photometric, render_pan  # noqa: E402

FRAME_SIZE = (540, 720)  # rendered portrait frames; the Localizer resizes to its working width


def geometric_jitter(sampler: CubeSampler, rng: np.random.Generator, hfov: float, size=FRAME_SIZE,
                     yaw: float | None = None) -> tuple[np.ndarray, dict]:
    """Random view with yaw/pitch/roll jitter, 2% scale and small image translation.
    Returns the image and its ground truth, corrected for the translation."""
    yaw = float(rng.uniform(0, 360)) if yaw is None else float(yaw)
    pitch = float(rng.uniform(-12, 12))
    roll = float(np.clip(rng.normal(0, 2.0), -5, 5))
    scale = float(rng.uniform(0.98, 1.02))
    eff_hfov = 2 * math.degrees(math.atan(math.tan(math.radians(hfov / 2)) / scale))
    img = sampler.render(yaw, pitch, roll, size, eff_hfov)
    w, h = size
    dx, dy = (float(v) for v in rng.uniform(-0.02, 0.02, 2) * np.array([w, h]))
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    img = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT101)
    f = (w / 2) / math.tan(math.radians(eff_hfov) / 2)
    gt = {"yaw": (yaw - math.degrees(math.atan(dx / f))) % 360, "pitch": pitch + math.degrees(math.atan(dy / f)),
          "roll": roll, "scale": scale, "dx": dx, "dy": dy}
    return img, gt


def _save(path: Path, img: np.ndarray) -> None:
    cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])


def build(data_dir: Path, out: Path, n_pos: int = 400, n_neg: int = 400, n_seq: int = 20,
          seq_pos_frames: int = 20, seq_neg_frames: int = 40, fps: float = 10.0, seed: int = 0,
          hfov: float = 55.0, size=FRAME_SIZE, holdout_every: int = 3) -> dict:
    rng = np.random.default_rng(seed)
    meta = load_pano_meta(data_dir)
    pid = meta["pano_id"]
    samplers = {pid: CubeSampler(load_faces(data_dir / "pano" / pid), load_layout(data_dir / "pano" / pid))}
    others = []
    for n in sorted(meta.get("negatives", []), key=lambda n: n.get("dist_m", 1e9)):
        d = data_dir / "pano" / n["pano_id"]
        if (d / "face_1.jpg").exists():
            samplers[n["pano_id"]] = CubeSampler(load_faces(d), load_layout(d))
            others.append(n)
    if not others:
        raise SystemExit("no other panoLocations with faces under data/pano/: hard negatives need them "
                         "(scripts/fetch_matterport.py downloads the nearest scan points)")
    holdout = {n["pano_id"] for i, n in enumerate(others) if holdout_every and i % holdout_every == 1}
    dist = {n["pano_id"]: float(n.get("dist_m", float("nan"))) for n in others}
    if out.exists():
        shutil.rmtree(out)
    (out / "frames").mkdir(parents=True)
    (out / "seq").mkdir()
    frames = []

    for i in range(n_pos):
        img, gt = geometric_jitter(samplers[pid], rng, hfov, size)
        img = photometric(img, rng, float(rng.uniform(0.3, 1.0)))
        fn = f"frames/pos_{i:04d}.jpg"
        _save(out / fn, img)
        frames.append({"file": fn, "label": "pos", "pano_id": pid, "dist_m": 0.0, **gt})

    n_neg = max(n_neg, 60)
    w = np.array([1.0 / (dist[n["pano_id"]] + 0.5) if math.isfinite(dist[n["pano_id"]]) else 1.0 for n in others])
    base = max(1, n_neg // (2 * len(others)))
    alloc = np.full(len(others), base)
    extra = n_neg - alloc.sum()
    if extra > 0:
        alloc += np.floor(extra * w / w.sum()).astype(int)
    alloc[0] += n_neg - alloc.sum()
    k = 0
    for n, cnt in zip(others, alloc):
        for _ in range(int(cnt)):
            img, gt = geometric_jitter(samplers[n["pano_id"]], rng, hfov, size)
            img = photometric(img, rng, float(rng.uniform(0.3, 1.0)))
            fn = f"frames/neg_{k:04d}.jpg"
            _save(out / fn, img)
            frames.append({"file": fn, "label": "neg", "pano_id": n["pano_id"], "dist_m": dist[n["pano_id"]],
                           "holdout": n["pano_id"] in holdout, **gt})
            k += 1

    for label in ("pos", "neg"):  # 75/25 split per class, fixed seed
        idx = [i for i, f in enumerate(frames) if f["label"] == label]
        perm = rng.permutation(len(idx))
        n_cal = int(round(0.75 * len(idx)))
        for j, p in enumerate(perm):
            frames[idx[p]]["split"] = "calib" if j < n_cal else "test"

    seqs = []
    for s in range(2 * n_seq):
        label = "pos" if s < n_seq else "neg"
        src = pid if label == "pos" else others[(s - n_seq) % len(others)]["pano_id"]
        nfr = seq_pos_frames if label == "pos" else seq_neg_frames
        speed = float(rng.uniform(5, 45))
        sweep = float(rng.uniform(40, 120)) if rng.random() < 0.5 else None
        sdir = out / "seq" / f"{label}_{s:02d}"
        sdir.mkdir()
        items = []
        for fr in render_pan(samplers[src], nfr, fps, float(rng.uniform(0, 360)), speed,
                             hfov=hfov, size=size, heading_offset=float(rng.uniform(0, 360)),
                             heading_noise=3.0, augment=0.5, seed=seed * 1000 + s, sweep=sweep):
            fn = f"seq/{label}_{s:02d}/{fr.i:03d}.jpg"
            _save(out / fn, fr.frame_rgb)
            items.append({"file": fn, "t": round(fr.t, 3), "heading_deg": round(fr.heading_deg, 2),
                          "yaw": round(fr.yaw, 3), "pitch": round(fr.pitch, 3)})
        seqs.append({"id": f"{label}_{s:02d}", "label": label, "pano_id": src, "fps": fps, "speed_dps": speed,
                     "sweep": sweep, "dist_m": 0.0 if label == "pos" else dist[src],
                     "holdout": src in holdout, "split": "test", "frames": items})

    manifest = {
        "simulated": True,
        "pano_id": pid,
        "label": meta.get("label"),
        "model_id": meta.get("model_id"),
        "hfov": hfov, "size": list(size), "seed": seed,
        "holdout_panos": sorted(holdout),
        "negative_panos": [n["pano_id"] for n in others],
        "counts": {
            "pos": n_pos, "neg": int(n_neg),
            "calib_pos": sum(f["split"] == "calib" and f["label"] == "pos" for f in frames),
            "calib_neg": sum(f["split"] == "calib" and f["label"] == "neg" for f in frames),
            "test_pos": sum(f["split"] == "test" and f["label"] == "pos" for f in frames),
            "test_neg": sum(f["split"] == "test" and f["label"] == "neg" for f in frames),
            "seq_pos": n_seq, "seq_neg": n_seq,
        },
        "frames": frames,
        "sequences": seqs,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--out", type=Path, default=None, help="default <data-dir>/synthetic")
    ap.add_argument("--n-pos", type=int, default=400)
    ap.add_argument("--n-neg", type=int, default=400)
    ap.add_argument("--n-seq", type=int, default=20)
    ap.add_argument("--seq-pos-frames", type=int, default=20, help="2 s at 10 fps: LOCKED must happen inside it")
    ap.add_argument("--seq-neg-frames", type=int, default=40)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--hfov", type=float, default=float(os.environ.get("HFOV_DEG", 55.0)))
    a = ap.parse_args(argv)
    out = a.out or a.data_dir / "synthetic"
    m = build(a.data_dir, out, a.n_pos, a.n_neg, a.n_seq, a.seq_pos_frames, a.seq_neg_frames, seed=a.seed,
              hfov=a.hfov)
    print(json.dumps({"out": str(out), "counts": m["counts"], "holdout_panos": m["holdout_panos"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
