"""Part 3 calibration: derive T_HI, T_LO, N_MIN (and PLACE_RATIO) from the CALIB split
only, and write data/calibration.json. The held-out test split is never read here.

  N_MIN, PLACE_RATIO  smallest values (N_MIN >= 30) that keep calib false matches at
                      zero even with a safety margin (N_MIN / 1.2, PLACE_RATIO / 1.15),
                      choosing the pair with the best calib recall.
  T_LO                candidate gate: keeps >= 99.5% of the calib positives' winning
                      candidates (minus 0.05), so it saves work without costing recall.
  T_HI                retrieval-only threshold at <= 1% calib false-match rate. Only
                      used by the ablation; retrieval never decides LOCKED.

Raw per-frame evidence is cached (data/eval/cache/), so re-running is cheap.

  python tools/calibrate.py
  python tools/calibrate.py --matcher sift --retriever auto
  python tools/calibrate.py --estimate-hfov data/onsite/pos   # HFOV from real frames
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from geometry import estimate_hfov, extract_features, intrinsics, normalize_contrast, verify  # noqa: E402
from localize import FrameEval, Localizer, Settings, make_embedder  # noqa: E402
from geometry import make_matcher  # noqa: E402
from refs import DATA_DIR  # noqa: E402

N_GRID = list(range(30, 401, 5)) + list(range(410, 1001, 10))
RATIO_GRID = [1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0]
N_MARGIN, RATIO_MARGIN = 1.2, 1.15
EVAL_VERSION = 3  # bump when per-frame evidence changes, so stale caches are not reused


def read_rgb(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(path)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def raw_settings(s: Settings) -> Settings:
    """Settings for raw evidence collection: no candidate gate, nothing decided."""
    return dataclasses.replace(s, T_LO=-1.01)


def build_localizer(data_dir: Path, s: Settings, matcher: str, retriever: str, competitors="auto",
                    verbose=True) -> Localizer:
    m, notes = make_matcher(matcher)
    e, notes2 = make_embedder(retriever)
    L = Localizer(data_dir, settings=s, matcher=m, embedder=e, competitors=competitors, verbose=verbose)
    L.notes = notes + notes2 + L.notes
    return L


def cache_file(cache_dir: Path, manifest: dict, L: Localizer, tag: str = "") -> Path:
    key = json.dumps([manifest.get("pano_id"), manifest.get("seed"), manifest.get("counts"), L.matcher.name,
                      L.embedder.name, L.competitors, L.s.HFOV_DEG, list(L.ref_size), tag, EVAL_VERSION])
    return cache_dir / f"raw_{hashlib.sha1(key.encode()).hexdigest()[:12]}.jsonl"


def raw_eval(L: Localizer, root: Path, items: list[dict], cache: Path | None, desc: str = "") -> list[FrameEval]:
    done: dict[str, dict] = {}
    if cache and cache.exists():
        for line in cache.read_text().splitlines():
            if line.strip():
                d = json.loads(line)
                done[d["file"]] = d["eval"]
    fh = None
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        fh = open(cache, "a")
    out = []
    t0 = time.time()
    try:
        for k, it in enumerate(items):
            if it["file"] in done:
                out.append(FrameEval.from_json(done[it["file"]]))
                continue
            fe = L.evaluate_frame(read_rgb(root / it["file"]), (), competitors="always")
            out.append(fe)
            if fh:
                fh.write(json.dumps({"file": it["file"], "eval": fe.to_json()}) + "\n")
                fh.flush()
            if (k + 1) % 50 == 0:
                print(f"  {desc} {k + 1}/{len(items)} ({time.time() - t0:.0f}s)", file=sys.stderr)
    finally:
        if fh:
            fh.close()
    return out


def rates(pos: list[FrameEval], neg: list[FrameEval], s: Settings, mode: str = "geometry") -> dict:
    tp = sum(fe.decide(s, mode)[0] for fe in pos)
    fp = sum(fe.decide(s, mode)[0] for fe in neg)
    return {
        "n_pos": len(pos), "n_neg": len(neg), "tp": int(tp), "fp": int(fp),
        "recall": tp / max(1, len(pos)),
        "false_match_rate": fp / max(1, len(neg)),
        "precision": tp / max(1, tp + fp),
    }


def _arrays(fes: list[FrameEval], t_lo: float):
    """Per frame: best demo inliers, its grid cells, best competitor inliers."""
    b = [fe.best_demo(t_lo) for fe in fes]
    inl = np.array([c.inliers if c else 0 for c in b])
    grid = np.array([c.grid if c else 0 for c in b])
    comp = np.array([fe.best_comp_inliers() for fe in fes])
    return inl, grid, comp


def _match(inl, grid, comp, n, r, g):
    return (inl >= n) & (grid >= g) & ((comp == 0) | (inl >= r * comp))


def choose(pos: list[FrameEval], neg: list[FrameEval], s0: Settings) -> tuple[dict, dict]:
    g = s0.GRID_CELLS_MIN
    pi, pg, pc = _arrays(pos, -2.0)
    ni, ng, nc = _arrays(neg, -2.0)
    best = None
    for r in RATIO_GRID:
        for n in N_GRID:
            if _match(ni, ng, nc, n / N_MARGIN, r / RATIO_MARGIN, g).any():
                continue
            rec = float(_match(pi, pg, pc, n, r, g).mean()) if len(pi) else 0.0
            key = (rec, -n, -r)
            if best is None or key > best[0]:
                best = (key, n, r)
            break  # larger n at this ratio only lowers recall
    feasible = best is not None
    if feasible:
        n_min, ratio = best[1], best[2]
    else:  # no zero-false-match setting on calib: record it, do not pretend
        n_min, ratio = N_GRID[-1], RATIO_GRID[-1]
    s = dataclasses.replace(s0, N_MIN=int(n_min), PLACE_RATIO=float(ratio))
    # T_LO from the sims of positives' winning candidates
    win = [fe.best_demo(-2.0) for fe in pos if fe.decide(dataclasses.replace(s, T_LO=-1.01))[0]]
    sims = np.array([c.sim for c in win if c is not None])
    t_lo = float(np.quantile(sims, 0.005) - 0.05) if len(sims) else -1.01
    s = dataclasses.replace(s, T_LO=round(t_lo, 4))
    # T_HI: retrieval-only, <= 1% false matches on calib negatives
    neg_scores = sorted((fe.top_any.sim if fe.top_any and fe.top_any.demo else -math.inf for fe in neg), reverse=True)
    allowed = int(math.floor(0.01 * len(neg_scores)))
    t_hi = neg_scores[allowed] + 1e-4 if neg_scores and math.isfinite(neg_scores[allowed]) else -1.0
    s = dataclasses.replace(s, T_HI=round(float(t_hi), 4))
    diag = {
        "feasible": feasible,
        "neg_max_inliers_passing_grid": int(ni[ng >= g].max()) if (ng >= g).any() else 0,
        "pos_inliers_p5": float(np.percentile(pi, 5)) if len(pi) else 0.0,
        "pos_inliers_median": float(np.median(pi)) if len(pi) else 0.0,
        "pos_ratio_p5": float(np.percentile(pi / np.maximum(pc, 1), 5)) if len(pi) else 0.0,
        "neg_ratio_max": float(np.max(ni / np.maximum(nc, 1))) if len(ni) else 0.0,
        "margins": {"N_MIN": N_MARGIN, "PLACE_RATIO": RATIO_MARGIN},
    }
    return {"N_MIN": int(n_min), "PLACE_RATIO": float(ratio), "T_LO": s.T_LO, "T_HI": s.T_HI}, diag


def hfov_from_frames(L: Localizer, frames_dir: Path) -> dict:
    """Estimate the phone's HFOV from real frames (e.g. warm-up captures)."""
    pairs, size = [], None
    for p in sorted(frames_dir.glob("*.jpg"))[:60]:
        img = L._prep(read_rgb(p))
        fe = L.evaluate_frame(img, (), competitors="if_needed")
        ok, b = fe.decide(L.s)
        if not ok:
            continue
        norm = normalize_contrast(img)
        h, w = norm.shape[:2]
        size = (w, h)
        feats = extract_features(L.matcher, norm)
        r = L._refs[b.idx]
        v = verify(L.matcher, feats, r.feats, intrinsics(w, h, L.s.HFOV_DEG), r.K)
        if v.live_pts is not None and len(v.live_pts) >= 20:
            pairs.append((v.live_pts, v.ref_pts, r.K))
    if not pairs:
        return {"frames_used": 0}
    hfov, res = estimate_hfov(pairs, size)
    return {"frames_used": len(pairs), "hfov_deg": round(hfov, 2), "median_residual_deg": round(res, 3)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--manifest", type=Path, default=None, help="default <data-dir>/synthetic/manifest.json")
    ap.add_argument("--out", type=Path, default=None, help="default <data-dir>/calibration.json")
    ap.add_argument("--matcher", default=os.environ.get("P3_MATCHER", "auto"))
    ap.add_argument("--retriever", default=os.environ.get("P3_RETRIEVER", "auto"))
    ap.add_argument("--hfov", type=float, default=float(os.environ.get("HFOV_DEG", 55.0)))
    ap.add_argument("--lock-need", type=int, default=2, help="3 = the 'require 3 of 3' fallback")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--estimate-hfov", type=Path, default=None, help="dir of real frames to estimate HFOV")
    a = ap.parse_args(argv)
    manifest_path = a.manifest or a.data_dir / "synthetic" / "manifest.json"
    out = a.out or a.data_dir / "calibration.json"
    s0 = dataclasses.replace(Settings.load(calibration={}, use_config=False, use_env=False),
                             HFOV_DEG=a.hfov, LOCK_NEED=a.lock_need)
    L = build_localizer(a.data_dir, raw_settings(s0), a.matcher, a.retriever)
    if a.estimate_hfov:
        print(json.dumps(hfov_from_frames(L, a.estimate_hfov), indent=1))
        return 0
    manifest = json.loads(manifest_path.read_text())
    root = manifest_path.parent
    calib = [f for f in manifest["frames"] if f["split"] == "calib"]  # never the test split
    cache = None if a.no_cache else cache_file(a.data_dir / "eval" / "cache", manifest, L)
    fes = raw_eval(L, root, calib, cache, "calib")
    pos = [fe for f, fe in zip(calib, fes) if f["label"] == "pos"]
    neg = [fe for f, fe in zip(calib, fes) if f["label"] == "neg"]
    thr, diag = choose(pos, neg, s0)
    s = dataclasses.replace(s0, **thr)
    geo, ret = rates(pos, neg, s, "geometry"), rates(pos, neg, s, "retrieval")
    cal = {
        **thr,
        "GRID_CELLS_MIN": s.GRID_CELLS_MIN, "YAW_AGREE_DEG": s.YAW_AGREE_DEG, "YAW_JUMP_DEG": s.YAW_JUMP_DEG,
        "LOCK_NEED": s.LOCK_NEED, "HFOV_DEG": s.HFOV_DEG,
        "matcher": L.matcher.name, "retriever": L.embedder.name, "competitors": L.competitors,
        "ref_size": list(L.ref_size), "pano_id": L.pano_id, "model_id": manifest.get("model_id"),
        "simulated": bool(manifest.get("simulated", True)), "split": "calib", "manifest": str(manifest_path),
        "source": "tools/calibrate.py", "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "calib": {"geometry": geo, "retrieval_only": ret, **diag}, "notes": L.notes,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cal, indent=1))
    print(json.dumps({k: cal[k] for k in ("N_MIN", "PLACE_RATIO", "T_LO", "T_HI", "matcher", "retriever")}, indent=1))
    print(f"calib split (SIMULATED): recall {geo['recall']:.3f}, false-match rate {geo['false_match_rate']:.4f} "
          f"({geo['fp']}/{geo['n_neg']}); retrieval-only false-match rate {ret['false_match_rate']:.4f}")
    if not diag["feasible"]:
        print("NO threshold pair gives zero calib false matches with margin: see NOTES fallbacks", file=sys.stderr)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
