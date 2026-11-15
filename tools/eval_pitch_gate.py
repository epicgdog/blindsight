"""Pitch gate ablation on the synthetic test walkthroughs (SIMULATED data, no server needed).

Runs every test sequence in data/synthetic/manifest.json through one multi-place Localizer
(all panos are candidate places), once without IMU pitch and once with pitch_deg = the frame's
true pitch + Gaussian noise. Reports, per condition:

  * walkthroughs that LOCKED on the right place / on a wrong place / never locked
  * frames LOCKED on the right place / on a wrong place
  * time to first correct LOCK
  * frames where the gate rejected a verified match, split by whether that match was the right place

  uv run python3 tools/eval_pitch_gate.py
  uv run python3 tools/eval_pitch_gate.py --noise 3 --limit 10
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
from localize import LOCKED, Localizer  # noqa: E402
from refs import DATA_DIR  # noqa: E402


def run(L: Localizer, root: Path, seqs: list[dict], pitch_noise: float | None, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    # Capture the verified best candidate after the pitch gate (multi mode passes it to _hold_place).
    if not getattr(L, "_eval_wrapped", False):
        orig = L._hold_place

        def hold(matched, best, **kw):
            L._eval_best = best
            return orig(matched, best, **kw)

        L._hold_place, L._eval_wrapped = hold, True
    rows = []
    for sq in seqs:
        L.reset()
        t, first_ok = 0.0, None
        ok = wrong = rej_right = rej_wrong = 0
        wrong_places: set[str] = set()
        for fr in sq["frames"]:
            img = read_rgb(root / fr["file"])
            pitch = None if pitch_noise is None else float(fr.get("pitch", 0.0)) + float(rng.normal(0, pitch_noise))
            t0 = time.perf_counter()
            loc = L.update(img, [], fr["heading_deg"], pitch_deg=pitch)
            dt = time.perf_counter() - t0
            t += max(1.0 / sq["fps"], dt)
            if L.last_debug.get("imu_rejected"):
                b = getattr(L, "_eval_best", None)
                if b is not None and b.place == sq["pano_id"]:
                    rej_right += 1
                else:
                    rej_wrong += 1
            if loc.state == LOCKED:
                if loc.pano_id == sq["pano_id"]:
                    ok += 1
                    if first_ok is None:
                        first_ok = t
                else:
                    wrong += 1
                    wrong_places.add(loc.pano_id)
        rows.append({"id": sq["id"], "pano_id": sq["pano_id"], "n": len(sq["frames"]), "locked_ok": ok,
                     "locked_wrong": wrong, "wrong_places": sorted(wrong_places), "first_ok_s": first_ok,
                     "rejected_right": rej_right, "rejected_wrong": rej_wrong})
        print(f"  {sq['id']}: ok {ok}/{len(sq['frames'])} wrong {wrong} rej {rej_right}/{rej_wrong} "
              f"first {first_ok}", file=sys.stderr, flush=True)
    lock_s = [r["first_ok_s"] for r in rows if r["first_ok_s"] is not None]
    return {
        "walkthroughs": len(rows),
        "walk_locked_right": sum(r["locked_ok"] > 0 for r in rows),
        "walk_locked_wrong": sum(r["locked_wrong"] > 0 for r in rows),
        "walk_never_right": sum(r["locked_ok"] == 0 for r in rows),
        "frames": sum(r["n"] for r in rows),
        "frames_locked_right": sum(r["locked_ok"] for r in rows),
        "frames_locked_wrong": sum(r["locked_wrong"] for r in rows),
        "first_lock_s_median": float(np.median(lock_s)) if lock_s else None,
        "gate_rejected_right_place": sum(r["rejected_right"] for r in rows),
        "gate_rejected_wrong_place": sum(r["rejected_wrong"] for r in rows),
        "detail": rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--noise", type=float, default=2.0, help="IMU pitch noise sigma, degrees")
    ap.add_argument("--limit", type=int, default=0, help="only the first N test sequences")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=None, help="default <data-dir>/eval/pitch_gate.json")
    a = ap.parse_args(argv)
    manifest_path = a.data_dir / "synthetic" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    seqs = [q for q in manifest["sequences"] if q["split"] == "test"]
    if a.limit:
        seqs = seqs[: a.limit]
    L = Localizer(a.data_dir, places="all")
    print(f"{len(L.candidate_places)} candidate places, {len(seqs)} walkthroughs", file=sys.stderr)
    res = {"simulated": True, "noise_deg": a.noise}
    for name, noise in (("pitch_off", None), ("pitch_on", a.noise)):
        print(f"[{name}]", file=sys.stderr)
        res[name] = run(L, manifest_path.parent, seqs, noise, a.seed)
    out = a.out or a.data_dir / "eval" / "pitch_gate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1))
    keys = ["walkthroughs", "walk_locked_right", "walk_locked_wrong", "walk_never_right", "frames",
            "frames_locked_right", "frames_locked_wrong", "first_lock_s_median",
            "gate_rejected_right_place", "gate_rejected_wrong_place"]
    print(f"SIMULATED, IMU pitch noise sigma {a.noise} deg")
    print(f"{'metric':32s} {'pitch off':>10s} {'pitch on':>10s}")
    for k in keys:
        print(f"{k:32s} {str(res['pitch_off'][k]):>10s} {str(res['pitch_on'][k]):>10s}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
