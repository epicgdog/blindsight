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
# Detailed mode only. More things you could walk into...
DETAILED_HAZARDS = frozenset({"bed", "toilet", "dog", "cat", "umbrella", "handbag", "fire hydrant"})
# ...and things that are not in the way but say what kind of place this is. They are
# named at the lowest priority with a long cooldown. Small items YOLO often mislabels
# (cup, bottle, book, phone, remote) stay silent.
MENTION = frozenset(
    {
        "laptop",
        "refrigerator",
        "sink",
        "microwave",
        "oven",
        "clock",
        "stop sign",
        "traffic light",
    }
)
MENTION_COOLDOWN_S = 30.0
MENTION_MIN_AREA = 0.01
SPOKEN_NAMES = {
    "dining table": "table",
    "potted plant": "plant",
    "tv": "TV",
    "refrigerator": "fridge",
    "handbag": "bag",
}
PLURALS = {"person": "people", "couch": "couches", "bench": "benches"}
# A box that grows this much within about a second is getting closer (whoever moves).
CLOSER_RATIO = 1.4
CLOSER_WINDOW_S = (0.6, 1.5)
TRACK_MAX_AGE_S = 0.7
# Spoken rate for estimating how long a cue keeps the phone talking. The phone drops a
# cue that arrives while it is speaking (unless it interrupts), so detailed mode waits.
WORDS_PER_S = 2.5
MAX_GROUP = 3


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


def _angle(cx, hfov):
    return math.degrees(math.atan((2 * cx - 1) * math.tan(math.radians(hfov / 2))))


def _clock(angle):
    return int(math.floor((angle + 15) / 30)) % 12 or 12


def counted(cls, count):
    """'a bench', 'an oven', '2 people'."""
    name = SPOKEN_NAMES.get(cls, cls)
    if count != 1:
        return f"{count} {PLURALS.get(cls, name + 's')}"
    return ("an " if name[0].lower() in "aeiou" else "a ") + name


def detailed_object(cls, count, cx, hfov, urgent=False, closer=False):
    """Camera-relative bearing only. Box size must never become a meter estimate."""
    angle = _angle(cx, hfov)
    name = SPOKEN_NAMES.get(cls, cls)
    subject = name[0].upper() + name[1:] if count == 1 else counted(cls, count)
    position = (
        "directly ahead"
        if abs(angle) <= 5
        else f"ahead to your {'left' if angle < 0 else 'right'}, about {_clock(angle)} o'clock"
    )
    return (
        ("Stop. " if urgent else "")
        + f"{subject} {position}"
        + (", getting closer." if closer and not urgent else ".")
    )


def short_object(cls, count, cx, hfov):
    """Compact form for several things in one sentence: 'a bench at 10 o'clock'."""
    angle = _angle(cx, hfov)
    where = "straight ahead" if abs(angle) <= 5 else f"at {_clock(angle)} o'clock"
    return f"{counted(cls, count)} {where}"


def spoken_list(items):
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def sentence(text):
    return text[0].upper() + text[1:] + "."


def speech_seconds(text):
    return len(text.split()) / WORDS_PER_S + 0.3


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


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
        self._speaking_until = -math.inf
        self._last_location = None
        self._pending_location = None
        self._pending_nav = None  # latest guidance instruction not yet spoken (priority 1)
        self._tracks: list[dict] = []
        self._prefix = uuid.uuid4().hex
        self._serial = 0

    def _spoken(self, cls):
        return cls in HAZARDS or (self.detailed and (cls in DETAILED_HAZARDS or cls in MENTION))

    def _getting_closer(self, detections, now_s):
        """Follow boxes across frames (same class, overlapping) and flag the ones that
        grew by CLOSER_RATIO within about a second. Boxes cut off by the image edge
        are never flagged: turning the phone makes those grow too."""
        tracks = [t for t in self._tracks if now_s - t["seen"] <= TRACK_MAX_AGE_S]
        pairs = sorted(
            (
                (_iou(t["bbox"], d.bbox), ti, di)
                for ti, t in enumerate(tracks)
                for di, d in enumerate(detections)
                if t["cls"] == d.cls
            ),
            reverse=True,
        )
        matched, used = {}, set()
        for score, ti, di in pairs:
            if score < 0.2:
                break
            if ti not in used and di not in matched:
                used.add(ti)
                matched[di] = tracks[ti]
        flags, kept = [], [t for ti, t in enumerate(tracks) if ti not in used]
        for di, d in enumerate(detections):
            x1, y1, x2, y2 = d.bbox
            area = (x2 - x1) * (y2 - y1)
            whole = x1 > 0.02 and x2 < 0.98
            track = matched.get(di) or {"cls": d.cls, "hist": []}
            hist = [h for h in track["hist"] if now_s - h[0] <= CLOSER_WINDOW_S[1]]
            hist.append((now_s, area, whole))
            track.update(bbox=d.bbox, seen=now_s, hist=hist)
            kept.append(track)
            old = [h for h in hist if now_s - h[0] >= CLOSER_WINDOW_S[0]]
            flags.append(
                len(hist) >= 3
                and bool(old)
                and whole
                and old[0][2]
                and area >= 0.02
                and area >= CLOSER_RATIO * old[0][1]
            )
        self._tracks = kept
        return flags

    def _in_view(self, current, objects):
        """'2 people, a bench and a TV' from the steadily seen objects, biggest first."""
        totals = {}
        for key, area in current.items():
            if self._consecutive.get(key, 0) < 2:
                continue
            count, best = totals.get(key[0], (0, 0.0))
            totals[key[0]] = (count + len(objects[key]), max(best, area))
        ranked = sorted(totals.items(), key=lambda item: (-item[1][1], item[0]))[:MAX_GROUP]
        return [counted(cls, count) for cls, (count, _) in ranked], {c for c, _ in ranked}

    def update(self, detections, location: Location, now_s: float, nav_text: str | None = None) -> Cue | None:
        """nav_text: a new guidance instruction (server.entrance_nav); spoken after hazards,
        before the location announcement. A newer instruction replaces an unspoken one."""
        if nav_text:
            self._pending_nav = nav_text
        current = {}
        objects = {}
        spoken = [d for d in detections if self._spoken(d.cls)]
        closer = self._getting_closer(spoken, now_s) if self.detailed else [False] * len(spoken)
        for detection, is_closer in zip(spoken, closer, strict=True):
            info = describe(detection, self.hfov_deg)
            x1, y1, x2, y2 = detection.bbox
            area = (x2 - x1) * (y2 - y1)
            if detection.cls in MENTION:
                if area < MENTION_MIN_AREA:
                    continue
                info["distance"] = ""  # never urgent, and a stable key while it is in view
            if info["distance"] is None:
                continue
            key = (detection.cls, info["direction"], info["distance"])
            current[key] = max(current.get(key, 0.0), area)
            objects.setdefault(key, []).append(((x1 + x2) / 2, area, is_closer))
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
        quiet = not self.detailed or now_s >= self._speaking_until - 1e-9

        candidates = []
        for key, area in current.items():
            cls, side, proximity = key
            matches = objects[key]
            is_closer = self.detailed and proximity != "very close" and any(m[2] for m in matches)
            if self._consecutive[key] < 3 and not is_closer:
                continue
            cue_key = (*key, "closer") if is_closer else key
            cooldown = MENTION_COOLDOWN_S if cls in MENTION else self.cooldown_s
            if now_s - self._last_key.get(cue_key, -math.inf) < cooldown:
                continue
            priority = 0 if proximity == "very close" else 1 if proximity == "close" else 3
            if cls in MENTION:
                priority = 4
            if is_closer:
                priority = min(priority, 1)
            gap = 0.8 if priority == 0 else self.min_gap_s
            if now_s - self._last_cue < gap - 1e-9 or (priority > 0 and not quiet):
                continue
            text = ", ".join(v for v in (SPOKEN_NAMES.get(cls, cls), side, proximity) if v)
            if self.detailed:
                text = detailed_object(
                    cls,
                    len(matches),
                    sum(m[0] for m in matches) / len(matches),
                    self.hfov_deg,
                    priority == 0,
                    is_closer,
                )
            candidates.append((priority, -area, cue_key, text))
        if self._pending_nav and now_s - self._last_cue >= self.min_gap_s - 1e-9 and quiet:
            candidates.append((1, 0.0, ("nav",), self._pending_nav))
        if self._pending_location and now_s - self._last_cue >= self.min_gap_s - 1e-9 and quiet:
            state_key, text = self._pending_location
            candidates.append((2, 0.0, ("location", *state_key), text))
        if (
            self.detailed
            and location.state == "LOCKED"
            and location.yaw_deg is not None
            and location.yaw_conf >= 0.7
            and now_s - self._last_cue >= self.min_gap_s
            and quiet
        ):
            for landmark in self.landmarks:
                if landmark["pano_id"] != location.pano_id:
                    continue
                relative = (landmark["yaw_deg"] - location.yaw_deg + 180) % 360 - 180
                clock = _clock(relative)
                key = ("landmark", location.pano_id, landmark["label"], clock)
                if now_s - self._last_key.get(key, -math.inf) < 45:
                    continue
                text = f"The scan shows {landmark['label']} at about {clock} o'clock."
                candidates.append((3, -0.001, key, text))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[3]))
        priority, _, key, text = candidates[0]
        said = [key]
        if self.detailed and key[0] == "location" and key[1] == "LOCKED":
            # Arriving: say what is around as well, then do not repeat it right away.
            names, classes = self._in_view(current, objects)
            if names:
                text += f" In view: {spoken_list(names)}."
                said += [k for k in current if k[0] in classes]
        elif self.detailed and priority >= 3 and len(key) == 3 and key[0] != "landmark":
            # Far things and things worth naming: up to MAX_GROUP in one sentence, so
            # one cue describes the scene instead of one object every few seconds.
            group = [
                c for c in candidates if c[0] >= 3 and len(c[2]) == 3 and c[2][0] != "landmark"
            ]
            group = group[:MAX_GROUP]
            if len(group) > 1:
                text = sentence(
                    spoken_list(
                        [
                            short_object(
                                c[2][0],
                                len(objects[c[2]]),
                                sum(m[0] for m in objects[c[2]]) / len(objects[c[2]]),
                                self.hfov_deg,
                            )
                            for c in group
                        ]
                    )
                )
                said = [c[2] for c in group]
        if key[-1] == "closer" and key[0] not in ("location", "landmark"):
            said.append(key[:3])  # no plain "Person directly ahead." right after
        if priority == 2:
            self._pending_location = None
            said = said[1:]
        elif key == ("nav",):
            self._pending_nav = None
            said = []
        for k in said:
            self._last_key[k] = now_s
        self._last_cue = now_s
        self._speaking_until = now_s + speech_seconds(text)
        self._serial += 1
        return Cue(f"{self._prefix}:{self._serial}", text, priority, priority == 0)
