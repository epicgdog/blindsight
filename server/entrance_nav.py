"""Demo guidance to the entrance over the floor-0 scan points.

Graph: scan points on floor 0 are nodes; two points are joined when they are at most
MAX_EDGE_M apart and the straight line between them crosses no wall segment from
data/matterport/walls_floor1.json (FALLBACK_EDGE_M if the goal is unreachable otherwise).
Route: Dijkstra from the decided place to the goal. Instructions use clock directions
relative to the user's heading in the mesh frame (x, y on the floor, z up).

Heading: the localizer's yaw (pano-local, from the matched reference view) turned into the
mesh frame with the calibrated pano frame (data/matterport/pano_frame.json, the same
transform the position solver uses). This is demo guidance, not verified safe navigation.
"""

from __future__ import annotations

import heapq
import json
import math
from pathlib import Path

import numpy as np

from server.minimap import scan_points

MAX_EDGE_M = 4.0
FALLBACK_EDGE_M = 6.0
WAYPOINT_M = 1.0
NEAR_PLACE_M = 1.5  # trust the tracked position only this close to the decided scan point
TURN_DEG = 30.0
REPEAT_S = 6.0

# pano-local (x right, y up, z forward at yaw 0) -> z-up frame; mirrors server/position.py.
_SWAP = {"a": np.array([[1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]]),
         "b": np.array([[-1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]])}


def _quat(q: dict) -> np.ndarray:
    x, y, z, w = (float(q[k]) for k in "xyzw")
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def frame_matrix(cand: dict, quat: dict | None) -> np.ndarray:
    Rq = _quat(quat) if quat is not None else np.eye(3)
    Rq = {"q": Rq, "q_inv": Rq.T, "none": np.eye(3)}[cand["quat"]]
    return Rq @ _rot_z(cand["base_deg"]) @ _SWAP[cand["swap"]]


def load_frames(data_dir: Path) -> dict[str, np.ndarray]:
    """pano id -> pano-local to mesh rotation; empty if the calibration files are missing."""
    try:
        cand = json.loads((data_dir / "matterport" / "pano_frame.json").read_text())["candidate"]
        rots = json.loads((data_dir / "matterport" / "pano_rotations.json").read_text())
    except (OSError, ValueError, KeyError):
        return {}
    return {pid: frame_matrix(cand, d.get("rotation")) for pid, d in rots.items()}


def mesh_heading_deg(M: np.ndarray, yaw_deg: float) -> float:
    """Camera forward (pano-local yaw) as a mesh-frame heading, clockwise from +y."""
    psi = math.radians(yaw_deg)
    w = M @ np.array([math.sin(psi), 0.0, math.cos(psi)])
    return math.degrees(math.atan2(w[0], w[1])) % 360


def bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360


def clock(turn_deg: float) -> int:
    return int(math.floor((turn_deg + 15) / 30)) % 12 or 12


def clear_line(a, b, walls: np.ndarray) -> bool:
    """True when segment a-b properly crosses no wall segment (rows x1, y1, x2, y2)."""
    if len(walls) == 0:
        return True
    p1, p2 = np.asarray(a, float), np.asarray(b, float)
    q1, q2 = walls[:, :2], walls[:, 2:]

    def cross(o, u, v):
        return (u[..., 0] - o[..., 0]) * (v[..., 1] - o[..., 1]) - (u[..., 1] - o[..., 1]) * (v[..., 0] - o[..., 0])

    d1, d2 = cross(q1, q2, p1[None]), cross(q1, q2, p2[None])
    d3, d4 = cross(p1[None], p2[None], q1), cross(p1[None], p2[None], q2)
    return not bool(np.any((d1 * d2 < 0) & (d3 * d4 < 0)))


def build_graph(points: dict[str, tuple[float, float]], walls: np.ndarray, max_edge: float) -> dict:
    ids = list(points)
    graph: dict[str, list[tuple[str, float]]] = {i: [] for i in ids}
    for k, a in enumerate(ids):
        for b in ids[k + 1:]:
            d = math.dist(points[a], points[b])
            if d <= max_edge and clear_line(points[a], points[b], walls):
                graph[a].append((b, d))
                graph[b].append((a, d))
    return graph


def shortest_path(graph: dict, start: str, goal: str) -> tuple[list[str], float] | None:
    if start not in graph or goal not in graph:
        return None
    queue, best = [(0.0, start, [start])], {start: 0.0}
    while queue:
        d, cur, path = heapq.heappop(queue)
        if cur == goal:
            return path, d
        if d > best[cur]:
            continue
        for nxt, w in graph[cur]:
            if d + w < best.get(nxt, math.inf):
                best[nxt] = d + w
                heapq.heappush(queue, (d + w, nxt, path + [nxt]))
    return None


class EntranceMap:
    """Floor-0 scan points, walls, the walk graph and the pano frames."""

    def __init__(self, points: dict[str, tuple[float, float]], labels: dict[str, str], walls: np.ndarray,
                 frames: dict[str, np.ndarray], goal: str | None = None, goal_label: str = "the entrance"):
        self.points, self.labels, self.walls, self.frames = points, labels, walls, frames
        self.goal, self.goal_label = goal, goal_label
        self.graph = build_graph(points, walls, MAX_EDGE_M)
        self.max_edge_m = MAX_EDGE_M
        if goal is not None and goal in points and self.unreachable(goal):
            self.graph = build_graph(points, walls, FALLBACK_EDGE_M)
            self.max_edge_m = FALLBACK_EDGE_M

    def unreachable(self, goal: str) -> list[str]:
        return [n for n in self.points if shortest_path(self.graph, n, goal) is None]

    @classmethod
    def load(cls, data_dir: Path, goal: str | None, goal_label: str = "the entrance") -> "EntranceMap":
        data_dir = Path(data_dir)
        sp = scan_points(data_dir)
        pts = [p for p in sp["points"] if p.get("floor") == 0]
        try:
            raw = json.loads((data_dir / "matterport" / "walls_floor1.json").read_text())
            walls = np.asarray(raw.get("segments") or [], float).reshape(-1, 4)
        except (OSError, ValueError):
            walls = np.zeros((0, 4))
        labels = {p["id"]: (f"scan point {p['label']}" if p.get("label") is not None else p["id"]) for p in pts}
        return cls({p["id"]: (float(p["x"]), float(p["y"])) for p in pts}, labels, walls,
                   load_frames(data_dir), goal, goal_label)

    def label(self, pid: str | None) -> str:
        if pid is None:
            return "unknown"
        return self.goal_label if pid == self.goal else self.labels.get(pid, pid)


class EntranceGuide:
    """Turns the decided place, position and heading into one spoken instruction at a time."""

    def __init__(self, emap: EntranceMap, repeat_s: float = REPEAT_S):
        self.map = emap
        self.repeat_s = repeat_s
        self.active = False
        self.route: list[str] = []
        self.idx = 0
        self.last_text: tuple | None = None  # (instruction, target) last spoken
        self.last_t = -math.inf
        self.status: dict = {"active": False}

    def start(self, start_place: str | None) -> dict:
        if self.map.goal not in self.map.points:
            return {"ok": False, "reason": "no entrance configured on this floor"}
        self.active = True
        self.route, self.idx, self.last_text, self.last_t = [], 0, None, -math.inf
        if start_place is not None:
            self._plan(start_place)
        self.status = {"active": True, "route": [self.map.label(p) for p in self.route]}
        out = {"ok": True, "destination": self.map.goal_label}
        if self.route:
            out.update(waypoints=len(self.route) - 1, distance_m=round(self._remaining(start_place), 1))
        else:
            out["note"] = "position not known yet; guidance starts when it is"
        return out

    def stop(self) -> dict:
        self.active, self.route, self.idx = False, [], 0
        self.status = {"active": False}
        return {"ok": True}

    def _plan(self, place: str) -> bool:
        found = shortest_path(self.map.graph, place, self.map.goal)
        if found is None:
            self.route, self.idx = [], 0
            return False
        self.route, self.idx = found[0], 0
        return True

    def _remaining(self, place: str | None, xy=None) -> float:
        if not self.route:
            return 0.0
        pts = self.map.points
        nxt = self.route[min(self.idx + 1, len(self.route) - 1)]
        here = xy or pts.get(place or self.route[self.idx], pts[self.route[self.idx]])
        d = math.dist(here, pts[nxt])
        for a, b in zip(self.route[self.idx + 1:], self.route[self.idx + 2:]):
            d += math.dist(pts[a], pts[b])
        return d

    def update(self, t: float, place: str | None, state: str, yaw_deg: float | None,
               position: dict | None) -> str | None:
        """Spoken instruction for this frame, or None (nothing new to say)."""
        if not self.active:
            return None
        text = self._instruction(place, state, yaw_deg, position)
        if text is None:
            return None
        key = (text, self.status.get("next"))  # same words toward a new point are a new instruction
        if key == self.last_text and t - self.last_t < self.repeat_s:
            return None
        if text.startswith("Paused") and self.last_text == key:
            return None  # say the pause once
        self.last_text, self.last_t = key, t
        if text.startswith("You're at"):
            self.stop()
            self.status = {"active": False, "arrived": True}
        return text

    def _instruction(self, place, state, yaw_deg, position) -> str | None:
        pts = self.map.points
        if state == "unknown" or place is None or place not in pts:
            self.status = {**self.status, "paused": True}
            return "Paused, finding your position. Look around slowly."
        if place == self.map.goal:
            return "You're at the entrance."
        if place not in self.route:
            if not self._plan(place):
                return "I can't find a route to the entrance from here."
        else:
            self.idx = max(self.idx, self.route.index(place))
        xy = pts[place]
        if position and position.get("x") is not None:
            p = (float(position["x"]), float(position["y"]))
            if math.dist(p, pts[place]) <= NEAR_PLACE_M:
                xy = p
        while self.idx + 1 < len(self.route) - 1 and math.dist(xy, pts[self.route[self.idx + 1]]) < WAYPOINT_M:
            self.idx += 1
        goal_xy = pts[self.map.goal]
        if math.dist(xy, goal_xy) < WAYPOINT_M:
            return "You're at the entrance."
        target = self.route[self.idx + 1]
        dist = math.dist(xy, pts[target])
        remaining = self._remaining(place, xy)
        self.status = {"active": True, "at": self.map.label(place), "next": self.map.label(target),
                       "next_m": round(dist, 1), "remaining_m": round(remaining, 1),
                       "waypoints_left": len(self.route) - 1 - self.idx}
        M = self.map.frames.get(place)
        if yaw_deg is None or M is None:
            return "Turn slowly in place until I tell you to go."
        heading = mesh_heading_deg(M, yaw_deg)
        turn = (bearing_deg(xy, pts[target]) - heading + 180) % 360 - 180
        self.status.update(heading_deg=round(heading), turn_deg=round(turn))
        if abs(turn) > TURN_DEG:
            side = "right" if turn > 0 else "left"
            return f"Turn {side}, to your {clock(turn)} o'clock."
        meters = max(1, round(dist))
        return f"Go straight, about {meters} meter{'s' if meters != 1 else ''}."
