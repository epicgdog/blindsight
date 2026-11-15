"""Obstacles on the floor from object detections, so the drawn route bends around them.

Where a detected object stands: the bottom centre of its box is where it meets the floor. That
pixel's ray, with the phone's pitch and roll and the camera about 1.4 m up, hits the floor at a
distance ahead and to the side; the tracked position and heading put it on the map.

Kept briefly: an object counts after MIN_HITS sightings close together, is forgotten when its
spot is in view and it is not seen MISSES_TO_FORGET frames in a row, or after its class's TTL
(people move: short). Pure, tested (server/tests/test_obstacles.py).

Error to expect: a 10 degree heading error moves an object about 0.5 m sideways at 3 m, and a
2 degree pitch error about 0.45 m in range at 4 m; the clearance below absorbs part of it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

CAMERA_HEIGHT_M = 1.4
# things that stand on the floor and block a walk, with a footprint radius (metres)
CLASS_RADIUS_M = {
    "person": 0.35, "chair": 0.4, "couch": 0.9, "bench": 0.8, "dining table": 0.8, "potted plant": 0.35,
    "suitcase": 0.35, "backpack": 0.3, "handbag": 0.25, "bicycle": 0.7, "dog": 0.4, "umbrella": 0.4,
    "bed": 1.0, "toilet": 0.4, "refrigerator": 0.5, "fire hydrant": 0.3,
}
CLEARANCE_M = 0.35  # kept free around an obstacle's footprint
TTL_S = {"person": 6.0}  # seconds since last seen
DEFAULT_TTL_S = 45.0
MIN_CONF = 0.4
MIN_HITS = 2
MERGE_M = 0.9  # a sighting this close to a known obstacle of the same class is the same one
MIN_RANGE_M, MAX_RANGE_M = 0.6, 6.0
VIEW_RANGE_M = (1.0, 5.0)  # an obstacle in view at this range and not seen counts a miss
MISSES_TO_FORGET = 3
SAME_FLOOR_M = 1.5  # camera heights further apart than this are different floors


def floor_point(bbox, w: int, h: int, pitch_deg: float | None, roll_deg: float | None, hfov_deg: float,
                height_m: float = CAMERA_HEIGHT_M) -> tuple[float, float] | None:
    """(forward, right) metres from the camera to where the box meets the floor, or None when the
    box bottom is cut off by the image edge (too close to tell) or the ray does not reach the floor."""
    x1, _, x2, y2 = bbox
    if y2 > 0.97:
        return None
    fx = (w / 2) / math.tan(math.radians(hfov_deg) / 2)
    du, dv = ((x1 + x2) / 2 - 0.5) * w, (y2 - 0.5) * h
    k = math.radians(roll_deg or 0.0)
    c, s = math.cos(k), math.sin(k)
    dx, dy = du * c - dv * s, du * s + dv * c  # undo the image roll
    xr, yd = dx / fx, dy / fx
    p = math.radians(pitch_deg or 0.0)  # + looks up
    horiz, vert = math.cos(p) + yd * math.sin(p), math.sin(p) - yd * math.cos(p)
    if vert >= -1e-3:
        return None
    t = height_m / -vert
    return t * horiz, t * xr


def to_map(f: float, r: float, x: float, y: float, heading_deg: float) -> tuple[float, float]:
    """Camera-relative (forward, right) -> map metres (x east, y north; heading clockwise from north)."""
    h = math.radians(heading_deg)
    return x + f * math.sin(h) + r * math.cos(h), y + f * math.cos(h) - r * math.sin(h)


@dataclass
class Obstacle:
    cls: str
    x: float
    y: float
    z: float  # camera height of the sighting (map frame), for the floor
    hits: int
    seen: float
    misses: int = 0

    @property
    def radius_m(self) -> float:
        return CLASS_RADIUS_M.get(self.cls, 0.35)

    def to_dict(self) -> dict:
        return {"cls": self.cls, "x": round(self.x, 2), "y": round(self.y, 2), "z": round(self.z, 2),
                "r_m": self.radius_m, "hits": self.hits}


class ObstacleMap:
    def __init__(self):
        self.items: list[Obstacle] = []

    def update(self, t: float, described: list[dict], frame_wh: tuple[int, int] | None,
               pitch_deg: float | None, roll_deg: float | None, position: dict | None,
               heading_deg: float | None, hfov_deg: float) -> None:
        """One frame's detections. Without a position, heading or frame size only ages the list."""
        hit: set[int] = set()
        ok = (frame_wh and position and position.get("x") is not None and position.get("y") is not None
              and heading_deg is not None and math.isfinite(heading_deg))
        if ok:
            px, py, pz = float(position["x"]), float(position["y"]), float(position.get("z") or 0.0)
            w, h = frame_wh
            for d in described:
                cls = d.get("cls")
                if cls not in CLASS_RADIUS_M or (d.get("conf") or 0.0) < MIN_CONF or not d.get("bbox"):
                    continue
                fp = floor_point(d["bbox"], w, h, pitch_deg, roll_deg, hfov_deg)
                if fp is None or not MIN_RANGE_M <= fp[0] <= MAX_RANGE_M:
                    continue
                x, y = to_map(fp[0], fp[1], px, py, heading_deg)
                near = [(math.dist((o.x, o.y), (x, y)), i) for i, o in enumerate(self.items)
                        if o.cls == cls and abs(o.z - pz) < SAME_FLOOR_M and i not in hit]
                near = [n for n in near if n[0] < MERGE_M]
                if near:
                    i = min(near)[1]
                    o = self.items[i]
                    k = 1.0 / min(o.hits + 1, 4)  # running mean that keeps following a moved object
                    o.x, o.y = o.x + (x - o.x) * k, o.y + (y - o.y) * k
                    o.hits, o.seen, o.misses = o.hits + 1, t, 0
                    hit.add(i)
                else:
                    self.items.append(Obstacle(cls, x, y, pz, 1, t))
                    hit.add(len(self.items) - 1)
            # known obstacles in view now but not seen: a miss each frame
            half = hfov_deg / 2 * 0.85
            for i, o in enumerate(self.items):
                if i in hit or abs(o.z - pz) >= SAME_FLOOR_M:
                    continue
                dx, dy = o.x - px, o.y - py
                hr = math.radians(heading_deg)
                f, r = dx * math.sin(hr) + dy * math.cos(hr), dx * math.cos(hr) - dy * math.sin(hr)
                if VIEW_RANGE_M[0] <= f <= VIEW_RANGE_M[1] and abs(math.degrees(math.atan2(r, f))) < half:
                    o.misses += 1
        self.items = [o for o in self.items
                      if o.misses < MISSES_TO_FORGET and t - o.seen <= TTL_S.get(o.cls, DEFAULT_TTL_S)]

    def active(self) -> list[Obstacle]:
        return [o for o in self.items if o.hits >= MIN_HITS]
