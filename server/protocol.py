"""The shared Part 1/2/3 contracts; binary framing follows plan section 5.1."""

import json
import math
import struct
from dataclasses import asdict, dataclass
from typing import Any

MAX_HEADER_BYTES = 16 * 1024
MAX_MESSAGE_BYTES = 4 * 1024 * 1024


class ProtocolError(ValueError):
    """A malformed client packet, safe to report without logging its contents."""


@dataclass
class Detection:
    cls: str
    conf: float
    bbox: tuple[float, float, float, float]

    def __post_init__(self):
        if len(self.bbox) != 4 or not all(math.isfinite(v) for v in self.bbox):
            raise ValueError("bbox must contain four finite coordinates")
        x1, y1, x2, y2 = self.bbox
        if not (0 <= x1 <= x2 <= 1 and 0 <= y1 <= y2 <= 1):
            raise ValueError("bbox must be ordered and normalized")
        if not self.cls or not math.isfinite(self.conf) or not 0 <= self.conf <= 1:
            raise ValueError("Invalid detection class or confidence")


@dataclass
class Location:
    pano_id: str | None
    label: str | None
    state: str
    score: float
    inliers: int
    yaw_deg: float | None
    yaw_conf: float
    best_ref: str | None
    # Additive, optional: preserves the nine-argument contract in the plan.
    ema: float | None = None

    def __post_init__(self):
        if self.state not in {"LOCKED", "UNCERTAIN", "LOST"}:
            raise ValueError("Invalid localization state")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["ema"] = self.score if self.ema is None else self.ema
        return result


@dataclass
class Cue:
    id: str
    text: str
    priority: int
    interrupt: bool


@dataclass(frozen=True)
class FrameHeader:
    seq: int
    ts_ms: float
    heading_deg: float | None
    w: int
    h: int

    def __post_init__(self):
        if type(self.seq) is not int or not 0 <= self.seq <= 2**53 - 1:
            raise ProtocolError("seq must be a nonnegative JavaScript-safe integer")
        if type(self.ts_ms) not in (int, float) or not math.isfinite(self.ts_ms) or self.ts_ms < 0:
            raise ProtocolError("ts_ms must be finite and nonnegative")
        if self.heading_deg is not None and (
            type(self.heading_deg) not in (int, float)
            or not math.isfinite(self.heading_deg)
            or not 0 <= self.heading_deg < 360
        ):
            raise ProtocolError("heading_deg must be null or in [0, 360)")
        if any(type(v) is not int or not 1 <= v <= 8192 for v in (self.w, self.h)):
            raise ProtocolError("w and h must be integers in [1, 8192]")


@dataclass(frozen=True)
class FramePacket:
    header: FrameHeader
    jpeg: bytes


def encode_frame(header: FrameHeader, jpeg: bytes) -> bytes:
    raw = json.dumps(asdict(header), separators=(",", ":"), allow_nan=False).encode("utf-8")
    packet = struct.pack(">I", len(raw)) + raw + jpeg
    decode_frame(packet)  # Both directions enforce the same contract.
    return packet


def decode_frame(data: bytes, max_bytes: int = MAX_MESSAGE_BYTES) -> FramePacket:
    if not isinstance(data, bytes) or not 6 <= len(data) <= max_bytes:
        raise ProtocolError("Invalid binary frame size")
    size = struct.unpack_from(">I", data)[0]
    if not 0 < size <= MAX_HEADER_BYTES or 4 + size >= len(data):
        raise ProtocolError("Invalid JSON header length")
    try:
        value = json.loads(data[4 : 4 + size])
        if not isinstance(value, dict):
            raise ProtocolError("Frame header must be an object")
        header = FrameHeader(
            **{key: value[key] for key in ("seq", "ts_ms", "heading_deg", "w", "h")}
        )
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise ProtocolError("Invalid frame header") from exc
    jpeg = data[4 + size :]
    if not jpeg.startswith(b"\xff\xd8"):
        raise ProtocolError("Frame payload must be JPEG")
    return FramePacket(header, jpeg)
