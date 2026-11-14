"""Local demo-session recording and streaming replay (no unbounded frame lists)."""

import json
import math
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .protocol import FrameHeader, FramePacket


class SessionRecorder:
    def __init__(self, root: Path):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        self.path = root / f"{stamp}-{uuid.uuid4().hex[:8]}"
        self.path.mkdir(parents=True)
        self._manifest = (self.path / "frames.jsonl").open("x", encoding="utf-8")
        self._start = None
        self._count = 0

    def write(self, packet: FramePacket, result: dict, received_s: float):
        if self._start is None:
            self._start = received_s
        filename = f"{self._count:08d}.jpg"
        (self.path / filename).write_bytes(packet.jpeg)
        row = {
            "version": 1,
            "file": filename,
            "offset_s": max(0.0, received_s - self._start),
            "header": asdict(packet.header),
            "result": result,
        }
        self._manifest.write(json.dumps(row, allow_nan=False) + "\n")
        self._manifest.flush()
        self._count += 1

    def close(self):
        self._manifest.close()


def replay_frames(path: Path):
    """Yield (offset seconds, packet); reject unsafe paths and non-monotonic times."""
    path = path.resolve()
    previous = -1.0
    with (path / "frames.jsonl").open(encoding="utf-8") as manifest:
        for line in manifest:
            row = json.loads(line)
            offset = float(row["offset_s"])
            if not math.isfinite(offset) or offset < 0 or offset < previous:
                raise ValueError("Replay offsets must be finite, nonnegative and monotonic")
            previous = offset
            image = (path / row["file"]).resolve()
            if image.parent != path or image.suffix.lower() != ".jpg":
                raise ValueError("Replay frame must be a JPEG within its session directory")
            if image.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Recorded frame exceeds protocol limit")
            yield offset, FramePacket(FrameHeader(**row["header"]), image.read_bytes())


def new_header(seq: int, w: int, h: int, heading_deg=None) -> FrameHeader:
    return FrameHeader(seq, time.time() * 1000, heading_deg, w, h)
