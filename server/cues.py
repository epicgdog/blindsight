"""Deterministic cue selection. Distances are box-area proxies, not metric depth."""

import math
import uuid

from .config import COOLDOWN_S, HFOV_DEG, MIN_GAP_S
from .protocol import Cue, Detection, Location

HAZARDS = frozenset(
    {
        "person",
        "chair",
        "couch",
        "bench",
        "dining table",
        "potted plant",
        "suitcase",
        "backpack",
        "bicycle",
        "tv",
    }
)
SPOKEN_NAMES = {"dining table": "table", "potted plant": "plant"}


def direction(cx_norm: float, hfov_deg: float = HFOV_DEG) -> str:
    angle = (cx_norm - 0.5) * hfov_deg
    side = "left" if angle < 0 else "right"
    if abs(angle) <= 8 + 1e-9:
        return "ahead"
    if abs(angle) <= 18 + 1e-9:
        return f"slightly {side}"
    return side


def distance(area: float, cls: str) -> str | None:
    if area >= 0.30 - 1e-12:
        return "very close"
    if area >= 0.10 - 1e-12:
        return "close"
    if area >= 0.03 - 1e-12 or cls == "person":
        return ""
    return None


def describe(detection: Detection, hfov_deg: float = HFOV_DEG) -> dict:
    x1, y1, x2, y2 = detection.bbox
    return {
        "cls": detection.cls,
        "conf": detection.conf,
        "bbox": list(detection.bbox),
        "direction": direction((x1 + x2) / 2, hfov_deg),
        "distance": distance((x2 - x1) * (y2 - y1), detection.cls),
    }


class CueEngine:
    def __init__(self, hfov_deg=HFOV_DEG, cooldown_s=COOLDOWN_S, min_gap_s=MIN_GAP_S):
        self.hfov_deg = hfov_deg
        self.cooldown_s = cooldown_s
        self.min_gap_s = max(0.8, min_gap_s)
        self._consecutive: dict[tuple, int] = {}
        self._last_key: dict[tuple, float] = {}
        self._last_cue = -math.inf
        self._last_location = None
        self._pending_location = None
        self._prefix = uuid.uuid4().hex
        self._serial = 0

    def update(self, detections, location: Location, now_s: float) -> Cue | None:
        current = {}
        for detection in detections:
            if detection.cls not in HAZARDS:
                continue
            info = describe(detection, self.hfov_deg)
            if info["distance"] is None:
                continue
            key = (detection.cls, info["direction"], info["distance"])
            x1, y1, x2, y2 = detection.bbox
            area = (x2 - x1) * (y2 - y1)
            current[key] = max(current.get(key, 0.0), area)
        # Count a key once per frame, regardless of how many objects share it.
        self._consecutive = {k: min(3, self._consecutive.get(k, 0) + 1) for k in current}
        state_key = (location.state, location.pano_id, location.label)
        if state_key != self._last_location:
            self._pending_location = None
            if location.state == "LOCKED":
                label = location.label or "the main lobby"
                self._pending_location = (state_key, f"You are in {label}")
            elif location.state == "LOST":
                self._pending_location = (state_key, "Not sure where you are")
            self._last_location = state_key

        candidates = []
        for key, area in current.items():
            if self._consecutive[key] < 3:
                continue
            if now_s - self._last_key.get(key, -math.inf) < self.cooldown_s:
                continue
            cls, side, proximity = key
            priority = 0 if proximity == "very close" else 1 if proximity == "close" else 3
            gap = 0.8 if priority == 0 else self.min_gap_s
            if now_s - self._last_cue < gap - 1e-9:
                continue
            text = ", ".join(v for v in (SPOKEN_NAMES.get(cls, cls), side, proximity) if v)
            candidates.append((priority, -area, key, text))
        if self._pending_location and now_s - self._last_cue >= self.min_gap_s - 1e-9:
            state_key, text = self._pending_location
            candidates.append((2, 0.0, ("location", *state_key), text))
        if not candidates:
            return None
        priority, _, key, text = min(candidates, key=lambda item: (item[0], item[1], item[3]))
        if priority == 2:
            self._pending_location = None
        else:
            self._last_key[key] = now_s
        self._last_cue = now_s
        self._serial += 1
        return Cue(f"{self._prefix}:{self._serial}", text, priority, priority == 0)
