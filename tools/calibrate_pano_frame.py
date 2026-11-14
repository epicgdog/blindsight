"""Find the pano-local -> mesh rotation (server/position.py frame candidates) from real data.

For pairs of nearby panos (A, B), views rendered from B's skybox are matched against A's
reference views. Ray casting A's matched pixels into the scan mesh under a candidate frame
M_A and solving PnP gives B's camera centre; the right candidate puts it on B's surveyed
position. The candidate with the lowest median error over all pairs wins.

  uv run python3 tools/calibrate_pano_frame.py
  uv run python3 tools/calibrate_pano_frame.py --pairs 12 --write data/matterport/pano_frame.json
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
from geometry import (  # noqa: E402
    SiftMatcher,
    bearings,
    cam_to_world,
    extract_features,
    intrinsics,
    normalize_contrast,
    verify,
)
from position import MeshDepth, PlaceFrame, cast_local, frame_candidates, frame_matrix, solve_pnp  # noqa: E402
from refs import DATA_DIR, CubeSampler, load_faces, load_layout  # noqa: E402

W, H, HFOV = 360, 480, 55.0
MIN_VIEW_INLIERS = 15
MESH = DATA_DIR / "matterport" / "R1FX6EmdcDt" / "50k_out_res_50k.obj"


def load_panos(data_dir: Path) -> dict:
    rot = json.loads((data_dir / "matterport" / "pano_rotations.json").read_text())
    out = {}
    for pid, d in rot.items():
        pdir = data_dir / "pano" / pid
        if (pdir / "face_1.jpg").exists():
            p = d["position"]
            out[pid] = {"pos": np.array([p["x"], p["y"], p["z"]]), "quat": d.get("rotation"), "dir": pdir}
    return out


def pick_pairs(panos: dict, n: int, dmin: float, dmax: float) -> list[tuple[str, str, float]]:
    ids = sorted(panos)
    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            d = panos[a]["pos"] - panos[b]["pos"]
            if abs(d[2]) < 0.5 and dmin <= np.linalg.norm(d) <= dmax:
                pairs.append((a, b, float(np.linalg.norm(d))))
    rng = np.random.default_rng(0)
    rng.shuffle(pairs)
    # spread over the building: at most one pair per pano A
    seen, out = set(), []
    for a, b, d in pairs:
        if a in seen:
            continue
        seen.add(a)
        out.append((a, b, d))
        if len(out) >= n:
            break
    return out


def best_views(matcher, sA: CubeSampler, sB: CubeSampler, K, n_views: int = 3):
    """(verification, ref_yaw) for the B views (every 30 deg) that match A's refs best."""
    refA = [(y, extract_features(matcher, normalize_contrast(sA.render(y, 0, 0, (W, H), HFOV))))
            for y in range(0, 360, 15)]
    found = []
    for yb in range(0, 360, 30):
        live = extract_features(matcher, normalize_contrast(sB.render(yb, 0, 0, (W, H), HFOV)))
        best = None
        for ya, fa in refA:
            v = verify(matcher, live, fa, K, K)
            if v.match_live_pts is not None and (best is None or v.h_inliers > best[0].h_inliers):
                best = (v, ya, yb)
        if best is not None and best[0].h_inliers >= MIN_VIEW_INLIERS:
            found.append(best)
    found.sort(key=lambda t: -t[0].h_inliers)
    return found[:n_views]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--mesh", type=Path, default=MESH)
    ap.add_argument("--pairs", type=int, default=8)
    ap.add_argument("--dmin", type=float, default=1.5)
    ap.add_argument("--dmax", type=float, default=4.0)
    ap.add_argument("--write", type=Path, default=None, help="write the winning candidate as JSON")
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    panos = load_panos(a.data_dir)
    depth = MeshDepth.load(a.mesh)
    matcher = SiftMatcher(nfeatures=3000)
    K = intrinsics(W, H, HFOV)
    pairs = pick_pairs(panos, a.pairs, a.dmin, a.dmax)
    print(f"{len(panos)} panos, {len(pairs)} pairs, mesh {len(depth.mesh.faces)} faces", file=sys.stderr)
    cands = frame_candidates()
    errs: dict[int, list[float]] = {i: [] for i in range(len(cands))}
    samplers: dict[str, CubeSampler] = {}

    def sampler(pid):
        if pid not in samplers:
            d = panos[pid]["dir"]
            samplers[pid] = CubeSampler(load_faces(d), load_layout(d))
        return samplers[pid]

    for A, B, dist in pairs:
        views = best_views(matcher, sampler(A), sampler(B), K)
        print(f"pair {A[:6]}->{B[:6]} {dist:.2f} m: {len(views)} views "
              f"(h_inliers {[v.h_inliers for v, _, _ in views]})", file=sys.stderr, flush=True)
        for v, ya, yb in views:
            C_ref = cam_to_world(ya, 0, 0)
            dirs = bearings(v.match_ref_pts, K) @ C_ref.T
            for i, c in enumerate(cands):
                M = frame_matrix(c, panos[A]["quat"])
                f = PlaceFrame(panos[A]["pos"], M)
                pts, ok = cast_local(depth, f, dirs)
                fix = solve_pnp(pts[ok], v.match_live_pts[ok], K) if ok.sum() >= 12 else None
                e = float(np.linalg.norm(f.to_depth(fix.local) - panos[B]["pos"])) if fix is not None else 99.0
                errs[i].append(e)
    rows = []
    for i, c in enumerate(cands):
        e = np.array(errs[i]) if errs[i] else np.array([99.0])
        rows.append({**c, "median_err_m": float(np.median(e)), "within_0_5m": int(np.sum(e < 0.5)), "n": len(e)})
    rows.sort(key=lambda r: r["median_err_m"])
    print(f"{'swap':4s} {'base':>4s} {'quat':>6s} {'median m':>9s} {'<0.5m':>6s}")
    for r in rows:
        print(f"{r['swap']:4s} {r['base_deg']:4d} {r['quat']:>6s} {r['median_err_m']:9.3f} {r['within_0_5m']:3d}/{r['n']}")
    print(f"{time.perf_counter() - t0:.0f} s", file=sys.stderr)
    if a.write:
        best = rows[0]
        a.write.write_text(json.dumps({"candidate": {k: best[k] for k in ("swap", "base_deg", "quat")},
                                       "median_err_m": best["median_err_m"], "runner_up": rows[1],
                                       "mesh": str(a.mesh.relative_to(ROOT) if a.mesh.is_absolute() else a.mesh),
                                       "pairs": [[x, y, d] for x, y, d in pairs],
                                       "by": "tools/calibrate_pano_frame.py (cross-pano PnP)"}, indent=1))
        print(f"wrote {a.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
