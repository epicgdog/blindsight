"""Detection descriptions for the voice agent: direction and a box-area distance proxy.

The old spoken cue engine is gone; the Live agent (server/agent.py) is the only voice.
Distances are box-area proxies, not metric depth.
"""

import math

from .config import HFOV_DEG
from .protocol import Detection

# Obstacles the agent warns about when very close (server/agent.py).
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
SPOKEN_NAMES = {
    "dining table": "table",
    "potted plant": "plant",
    "tv": "TV",
    "refrigerator": "fridge",
    "handbag": "bag",
}


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
        "bearing_deg": round(
            math.degrees(math.atan((x1 + x2 - 1) * math.tan(math.radians(hfov_deg / 2)))), 1
        ),
        "distance_source": "image_area_proxy",
        "distance_m": None,
    }
