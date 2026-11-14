"""Retrieval-only A/B of global embedders (no geometry, no thresholds).

For every synthetic frame whose source pano is a candidate place, rank the places by their
best reference similarity and report recall@1/@3, the similarity margin of the true place over
the best wrong one, and embedding time per frame. Nothing is tuned here.

  uv run python3 tools/eval_retrieval.py
  uv run python3 tools/eval_retrieval.py --retrievers dinov2-small dinov2-base --split test
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))
from calibrate import read_rgb  # noqa: E402
from geometry import normalize_contrast  # noqa: E402
from localize import Localizer, make_embedder, make_matcher  # noqa: E402
from refs import DATA_DIR  # noqa: E402


def place_scores(L: Localizer, q: np.ndarray, places: list[str], ref_place: np.ndarray) -> np.ndarray:
    sims = L._emb @ q
    out = np.full(len(places), -2.0, np.float32)
    for k in range(len(places)):
        m = ref_place == k
        if m.any():
            out[k] = sims[m].max()
    return out


def run(retriever: str, data_dir: Path, frames: list[dict], root: Path, matcher) -> dict:
    emb, notes = make_embedder(retriever)
    t0 = time.perf_counter()
    L = Localizer(data_dir, matcher=matcher, embedder=emb, competitors=None, places="all", verbose=False)
    startup = time.perf_counter() - t0
    places = list(L.candidate_places)
    index = {p: k for k, p in enumerate(places)}
    pano_refs = np.array([r.demo and r.view.source == "pano" for r in L._refs])
    ref_place = np.array([index.get(r.place, -1) if ok else -1 for r, ok in zip(L._refs, pano_refs)])
    hits1 = hits3 = 0
    margins, times = [], []
    used = [f for f in frames if f["pano_id"] in index]
    for f in used:
        img = normalize_contrast(L._prep(read_rgb(root / f["file"])))
        t = time.perf_counter()
        q = emb.embed_batch([img])[0]
        times.append(time.perf_counter() - t)
        s = place_scores(L, q, places, ref_place)
        true = index[f["pano_id"]]
        order = np.argsort(-s)
        rank = int(np.where(order == true)[0][0])
        hits1 += rank == 0
        hits3 += rank < 3
        margins.append(float(s[true] - np.max(np.delete(s, true))))
    n = max(1, len(used))
    return {
        "retriever": emb.name, "dim": int(L._emb.shape[1]), "places": len(places), "frames": len(used),
        "recall@1": hits1 / n, "recall@3": hits3 / n,
        "margin_median": float(np.median(margins)) if margins else None,
        "margin_p10": float(np.percentile(margins, 10)) if margins else None,
        "embed_ms_median": 1000 * float(np.median(times[3:] or times)) if times else None,
        "startup_s": round(startup, 1), "notes": notes,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--manifest", type=Path, default=None, help="default <data-dir>/synthetic/manifest.json")
    ap.add_argument("--retrievers", nargs="+", default=["dinov2-small", "dinov2-base"])
    ap.add_argument("--split", default="all", help="calib, test or all")
    ap.add_argument("--matcher", default="lightglue")
    ap.add_argument("--out", type=Path, default=None, help="default <data-dir>/eval/retrieval.json")
    a = ap.parse_args()
    mpath = a.manifest or a.data_dir / "synthetic" / "manifest.json"
    manifest = json.loads(mpath.read_text())
    frames = [f for f in manifest["frames"] if a.split == "all" or f.get("split") == a.split]
    matcher, _ = make_matcher(a.matcher)
    results = []
    for r in a.retrievers:
        res = run(r, a.data_dir, frames, mpath.parent, matcher)
        results.append(res)
        print(f"{res['retriever']:>14}  dim {res['dim']:>5}  places {res['places']}  frames {res['frames']}  "
              f"R@1 {res['recall@1']:.3f}  R@3 {res['recall@3']:.3f}  "
              f"margin med {res['margin_median']:+.3f} p10 {res['margin_p10']:+.3f}  "
              f"embed {res['embed_ms_median']:.1f} ms  startup {res['startup_s']}s", flush=True)
    out = a.out or a.data_dir / "eval" / "retrieval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"split": a.split, "results": results}, indent=1))


if __name__ == "__main__":
    main()
