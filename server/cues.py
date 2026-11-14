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
        "bearing_deg": round(
            math.degrees(math.atan((x1 + x2 - 1) * math.tan(math.radians(hfov_deg / 2)))), 1
        ),
        "distance_source": "image_area_proxy",
        "distance_m": None,
    }


def detailed_object(cls, count, cx, hfov, urgent=False):
    """Camera-relative bearing only. Box size must never become a meter estimate."""
    angle = math.degrees(math.atan((2 * cx - 1) * math.tan(math.radians(hfov / 2))))
    clock = int(math.floor((angle + 15) / 30)) % 12 or 12
    names = {"person": "people", "couch": "couches", "bench": "benches"}
    name = SPOKEN_NAMES.get(cls, cls)
    subject = name.capitalize() if count == 1 else f"{count} {names.get(cls, name + 's')}"
    position = (
        "directly ahead"
        if abs(angle) <= 5
        else f"ahead to your {'left' if angle < 0 else 'right'}, about {clock} o'clock"
    )
    return ("Stop. " if urgent else "") + f"{subject} {position}."


class CueEngine:
    def __init__(
        self,
        hfov_deg=HFOV_DEG,
        cooldown_s=COOLDOWN_S,
        min_gap_s=MIN_GAP_S,
        *,
        detailed=False,
        landmarks=(),
    ):
        self.detailed = detailed
        self.landmarks = [
            x
            for x in landmarks
            if isinstance(x, dict)
            and x.get("reviewed") is True
            and isinstance(x.get("label"), str)
            and 0 < len(x["label"]) <= 100
            and isinstance(x.get("yaw_deg"), (int, float))
            and math.isfinite(x["yaw_deg"])
            and 0 <= x["yaw_deg"] < 360
            and isinstance(x.get("pano_id"), str)
        ]
        self.hfov_deg = hfov_deg
        self.cooldown_s = cooldown_s
        self.min_gap_s = max(0.8, min_gap_s)
        self._consecutive: dict[tuple, int] = {}
        self._last_key: dict[tuple, float] = {}
        self._last_cue = -math.inf
        self._last_location = None
        self._pending_location = None
        self._pending_nav = None  # latest guidance instruction not yet spoken (priority 1)
        self._prefix = uuid.uuid4().hex
        self._serial = 0

    def update(self, detections, location: Location, now_s: float, nav_text: str | None = None) -> Cue | None:
        """nav_text: a new guidance instruction (server.entrance_nav); spoken after hazards,
        before the location announcement. A newer instruction replaces an unspoken one."""
        if nav_text:
            self._pending_nav = nav_text
        current = {}
        objects = {}
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
            objects.setdefault(key, []).append(((x1 + x2) / 2, area))
        # Count a key once per frame, regardless of how many objects share it.
        self._consecutive = {k: min(3, self._consecutive.get(k, 0) + 1) for k in current}
        state_key = (location.state, location.pano_id, location.label)
        if state_key != self._last_location:
            self._pending_location = None
            if location.state == "LOCKED":
                label = location.label or "the main lobby"
                text = (
                    f"View matches {location.label}."
                    if location.label
                    else "Reference view matched. Room name unverified."
                )
                self._pending_location = (
                    state_key,
                    text if self.detailed else f"You are in {label}",
                )
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
            if self.detailed:
                matches = objects[key]
                text = detailed_object(
                    cls,
                    len(matches),
                    sum(x for x, _ in matches) / len(matches),
                    self.hfov_deg,
                    priority == 0,
                )
            candidates.append((priority, -area, key, text))
        if self._pending_nav and now_s - self._last_cue >= self.min_gap_s - 1e-9:
            candidates.append((1, 0.0, ("nav",), self._pending_nav))
        if self._pending_location and now_s - self._last_cue >= self.min_gap_s - 1e-9:
            state_key, text = self._pending_location
            candidates.append((2, 0.0, ("location", *state_key), text))
        if (
            self.detailed
            and location.state == "LOCKED"
            and location.yaw_deg is not None
            and location.yaw_conf >= 0.7
            and now_s - self._last_cue >= self.min_gap_s
        ):
            for landmark in self.landmarks:
                if landmark["pano_id"] != location.pano_id:
                    continue
                relative = (landmark["yaw_deg"] - location.yaw_deg + 180) % 360 - 180
                clock = int(math.floor((relative + 15) / 30)) % 12 or 12
                key = ("landmark", location.pano_id, landmark["label"], clock)
                if now_s - self._last_key.get(key, -math.inf) < 45:
                    continue
                text = f"The scan shows {landmark['label']} at about {clock} o'clock."
                candidates.append((3, -0.001, key, text))
        if not candidates:
            return None
        priority, _, key, text = min(candidates, key=lambda item: (item[0], item[1], item[3]))
        if priority == 2:
            self._pending_location = None
        elif key == ("nav",):
            self._pending_nav = None
        else:
            self._last_key[key] = now_s
        self._last_cue = now_s
        self._serial += 1
        return Cue(f"{self._prefix}:{self._serial}", text, priority, priority == 0)
