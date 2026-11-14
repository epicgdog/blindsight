"""What the phone would have said during a recorded walk, old wording next to new.

A server started with RECORD=1 saves every phone frame with its detections and location
in data/sessions/<stamp>/frames.jsonl. This replays those through the cue engine twice
(DETAILED_CUES=0 and detailed) without the camera, YOLO or the localizer:

  python -m server.cue_replay                      # newest session in data/sessions
  python -m server.cue_replay data/sessions/<stamp>
"""

import argparse
import json
import sys
from pathlib import Path

from .config import COOLDOWN_S, HFOV_DEG, MIN_GAP_S, ROOT
from .cues import CueEngine
from .protocol import Detection, Location

LOCATION_FIELDS = (
    "pano_id",
    "label",
    "state",
    "score",
    "inliers",
    "yaw_deg",
    "yaw_conf",
    "best_ref",
    "ema",
)


def recorded(session: Path):
    """(seconds since start, detections, location) per recorded frame."""
    with (session / "frames.jsonl").open(encoding="utf-8") as manifest:
        for line in manifest:
            row = json.loads(line)
            result = row["result"]
            detections = [
                Detection(d["cls"], float(d["conf"]), tuple(d["bbox"]))
                for d in result.get("detections", [])
            ]
            loc = result["location"]
            location = Location(**{k: loc.get(k) for k in LOCATION_FIELDS if k in loc})
            yield float(row["offset_s"]), detections, location


def replay(session: Path, landmarks=(), hfov_deg=HFOV_DEG, out=sys.stdout) -> int:
    old = CueEngine(hfov_deg, COOLDOWN_S, MIN_GAP_S, detailed=False)
    new = CueEngine(hfov_deg, COOLDOWN_S, MIN_GAP_S, detailed=True, landmarks=landmarks)
    counts = [0, 0, 0]
    for offset, detections, location in recorded(session):
        counts[0] += 1
        said = [engine.update(detections, location, offset) for engine in (old, new)]
        for k, (who, cue) in enumerate(zip(("before", "now   "), said, strict=True)):
            if cue:
                counts[k + 1] += 1
                stamp = f"{offset:7.1f}s" if not (k and said[0]) else " " * 8
                print(f"{stamp}  {who}  {cue.text}", file=out)
    print(
        f"\n{counts[0]} frames: {counts[1]} cues before, {counts[2]} cues now.",
        file=out,
    )
    return 0


def newest(sessions: Path) -> Path | None:
    found = sorted(p for p in sessions.glob("*/frames.jsonl"))
    return found[-1].parent if found else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session", nargs="?", type=Path, help="data/sessions/<stamp> (default: newest)")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    a = ap.parse_args(argv)
    session = a.session or newest(a.data_dir / "sessions")
    if session is None or not (session / "frames.jsonl").is_file():
        print(
            "no recorded session: start the server with RECORD=1, walk with the phone, "
            "then copy data/sessions/<stamp> here",
            file=sys.stderr,
        )
        return 1
    try:
        landmarks = json.loads((a.data_dir / "cue_landmarks.json").read_text())
    except (OSError, ValueError):
        landmarks = []
    print(f"session {session}\n")
    return replay(session, landmarks if isinstance(landmarks, list) else [])


if __name__ == "__main__":
    raise SystemExit(main())
