"""Metric position check on the synthetic test frames (SIMULATED data, no server needed).

Each synthetic frame is rendered from a pano centre (with small image-plane jitter), so the
true camera centre is that pano's surveyed position. Runs every test frame through a
multi-place Localizer and compares last_debug["match"]["position"] with it.

  uv run python3 tools/eval_position.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "tools"))
from calibrate import read_rgb  # noqa: E402
from localize import Localizer  # noqa: E402
from position import PositionSolver  # noqa: E402
from refs import DATA_DIR  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", type=Path, default=None, help="default <data-dir>/eval/position.json")
    a = ap.parse_args(argv)
    mpath = a.data_dir / "synthetic" / "manifest.json"
    manifest = json.loads(mpath.read_text())
    frames = [f for f in manifest["frames"] if f["split"] == "test"]
    if a.limit:
        frames = frames[: a.limit]
    rot = json.loads((a.data_dir / "matterport" / "pano_rotations.json").read_text())
    truth = {k: np.array([v["position"][c] for c in "xyz"]) for k, v in rot.items()}
    L = Localizer(a.data_dir, places="all")
    if L.position is None:
        print("position solver is off: " + "; ".join(L.notes), file=sys.stderr)
        return 1
    L.position = PositionSolver.load(a.data_dir, async_cast=False)  # offline: wait for every cast
    rows = []
    for f in frames:
        L.reset()
        loc = L.update(read_rgb(mpath.parent / f["file"]), [], None)
        dbg = L.last_debug
        p = (dbg.get("match") or {}).get("position")
        row = {"file": f["file"], "pano_id": f["pano_id"], "matched": bool(dbg.get("matched")),
               "place": loc.pano_id, "position": p, "ms": dbg.get("total")}
        if p is not None and f["pano_id"] in truth:
            row["err_m"] = float(np.linalg.norm(np.array([p["x"], p["y"], p["z"]]) - truth[f["pano_id"]]))
            row["err_xy_m"] = float(np.linalg.norm(np.array([p["x"], p["y"]]) - truth[f["pano_id"]][:2]))
            row["right_place"] = p["place"] == f["pano_id"]
        rows.append(row)
    matched = [r for r in rows if r["matched"]]
    fixed = [r for r in rows if "err_m" in r]
    e = np.array([r["err_m"] for r in fixed]) if fixed else np.array([np.nan])
    exy = np.array([r["err_xy_m"] for r in fixed]) if fixed else np.array([np.nan])
    res = {"simulated": True, "frames": len(rows), "matched": len(matched), "with_position": len(fixed),
           "right_place": sum(r.get("right_place", False) for r in fixed),
           "err_m_median": float(np.median(e)), "err_m_p90": float(np.percentile(e, 90)),
           "err_xy_m_median": float(np.median(exy)), "err_xy_m_p90": float(np.percentile(exy, 90)),
           "within_0_25m": int(np.sum(e < 0.25)), "within_0_5m": int(np.sum(e < 0.5)),
           "frame_ms_median": float(np.median([r["ms"] for r in rows if r["ms"] is not None])),
           "detail": rows}
    out = a.out or a.data_dir / "eval" / "position.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1))
    for k, v in res.items():
        if k != "detail":
            print(f"{k:18s} {v}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
