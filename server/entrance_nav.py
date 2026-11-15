"""Demo guidance to the entrance over the floor-0 scan points.

Graph: scan points on floor 0 are nodes; two points are joined when they are at most
MAX_EDGE_M apart and the straight line between them crosses no wall segment from
data/matterport/walls_floor1.json (FALLBACK_EDGE_M if the goal is unreachable otherwise).
Route: Dijkstra from the decided place to the goal, as a server.route.Route (turns, distances,
progress). Instructions use clock directions relative to the user's heading in the mesh frame
(x, y on the floor, z up).

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
from server.route import AROUND_DEG, STRIDE_M, TURN_DEG, Progress, Route, clock, turn_between

MAX_EDGE_M = 4.0
FALLBACK_EDGE_M = 6.0
WAYPOINT_M = 1.0
NEAR_PLACE_M = 1.5  # an unsure tracked position is trusted only this close to the decided scan point
TRACK_SIGMA_M = 1.5  # a tracked position at least this sure is used wherever it is (and is trusted)
REPEAT_S = 20.0  # an unchanged instruction is repeated once after this long
FIVE_M = 5.0  # "In 5 meters, turn left" is said this far before a turn
NOW_M = 1.5  # "Turn left now" is said this close to a turn
ARRIVE_M = 1.5  # "You're at the entrance." is said this close to the goal
TURN_WAIT_S = 6.0  # after "turn now", wait this long (or until turned) before checking the heading
ARRIVED = "You're at the entrance."

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


def start_point(emap: EntranceMap, place: str, position: dict | None) -> tuple[tuple[float, float], str]:
    """Where the person stands (xy) and the map node to route from.

    The Kalman-tracked position wins when its sigma is at most TRACK_SIGMA_M; the route then starts
    at the nearest scan point to it (the decided one when that is close). Otherwise the decided scan
    point, nudged to an unsure tracked position only when that is within NEAR_PLACE_M of it."""
    pts = emap.points
    xy = pts[place]
    if not position or position.get("x") is None:
        return xy, place
    p = (float(position["x"]), float(position["y"]))
    sigma = position.get("sigma_m")
    if sigma is not None and float(sigma) <= TRACK_SIGMA_M:
        if math.dist(p, pts[place]) <= NEAR_PLACE_M:
            return p, place
        node = min((n for n in pts if n in emap.graph), key=lambda n: math.dist(p, pts[n]), default=place)
        return p, node
    if math.dist(p, pts[place]) <= NEAR_PLACE_M:
        return p, place
    return xy, place


def _trusted(position: dict | None) -> bool:
    """A metric position good enough to measure progress and off-route with."""
    if not position or position.get("x") is None or position.get("sigma_m") is None:
        return False
    return float(position["sigma_m"]) <= TRACK_SIGMA_M


CLEARANCE_M = 0.4  # a shortcut must keep this far from walls on both sides (half a body plus margin)


def clear_wide(a, b, walls: np.ndarray, half: float = CLEARANCE_M) -> bool:
    """clear_line for a corridor: the centre line and both lines offset by `half` cross no wall."""
    if not clear_line(a, b, walls):
        return False
    d = np.asarray(b, float) - np.asarray(a, float)
    n = float(np.hypot(*d))
    if n < 1e-6:
        return True
    off = np.array([-d[1], d[0]]) / n * half
    pa, pb = np.asarray(a, float), np.asarray(b, float)
    return clear_line(pa + off, pb + off, walls) and clear_line(pa - off, pb - off, walls)


def straighten(coords: list, walls: np.ndarray) -> list:
    """String pulling: from each kept point, jump to the farthest later point with a clear,
    wide line of sight. A straight shot when nothing is in the way; corners only at walls."""
    if len(coords) <= 2:
        return list(coords)
    out, i = [coords[0]], 0
    while i < len(coords) - 1:
        j = len(coords) - 1
        while j > i + 1 and not clear_wide(coords[i], coords[j], walls):
            j -= 1
        out.append(coords[j])
        i = j
    return out


def plan(emap: EntranceMap, xy, node: str) -> Route | None:
    """Route from xy (standing next to map node `node`) to the destination, or None if unreachable.
    Scan point path to the destination's node, then a straight last leg to a dropped pin; the
    whole path is straightened so it bends only where walls are in the way."""
    found = shortest_path(emap.graph, node, emap.goal)
    if found is None:
        return None
    coords = [xy] + [emap.points[n] for n in found[0]]
    if math.dist(coords[-1], emap.goal_xy) > 0.05:
        coords.append(emap.goal_xy)
    return Route(straighten(coords, emap.walls))


def steps_text(meters: float, stride: float) -> str:
    """A distance as the person's own steps: easier to judge without sight than metres."""
    n = max(1, round(meters / max(0.3, stride)))
    return f"{n} step{'s' if n != 1 else ''}"


class EntranceMap:
    """Floor-0 scan points, walls, the walk graph and the pano frames."""

    def __init__(self, points: dict[str, tuple[float, float]], labels: dict[str, str], walls: np.ndarray,
                 frames: dict[str, np.ndarray], goal: str | None = None, goal_label: str = "the entrance"):
        self.points, self.labels, self.walls, self.frames = points, labels, walls, frames
        self.goal, self.goal_label = goal, goal_label
        # The destination: graph node the route ends on (goal), the point itself (goal_xy) and its
        # kind. The entrance is a scan point; a dropped pin is any point near one.
        self.entrance, self.entrance_label = goal, goal_label
        self.goal_xy = points.get(goal) if goal is not None else None
        self.goal_kind = "entrance"
        self.graph = build_graph(points, walls, MAX_EDGE_M)
        self.max_edge_m = MAX_EDGE_M
        if goal is not None and goal in points and self.unreachable(goal):
            self.graph = build_graph(points, walls, FALLBACK_EDGE_M)
            self.max_edge_m = FALLBACK_EDGE_M

    def unreachable(self, goal: str) -> list[str]:
        return [n for n in self.points if shortest_path(self.graph, n, goal) is None]

    @classmethod
    def load(cls, data_dir: Path, goal: str | None, goal_label: str = "the entrance") -> EntranceMap:
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

    def pin_node(self, xy) -> tuple[str, tuple[float, float], bool] | None:
        """The scan point a route to `xy` ends its graph part on, the reachable point, and whether
        the point was moved. Nearest scan point with a clear line to xy within max_edge_m; with
        none (inside a wall, outside the scan), xy moves onto the nearest scan point."""
        if not self.points:
            return None
        p = (float(xy[0]), float(xy[1]))
        by_dist = sorted(self.points, key=lambda n: math.dist(p, self.points[n]))
        for n in by_dist:
            if math.dist(p, self.points[n]) > self.max_edge_m:
                break
            if n in self.graph and clear_line(self.points[n], p, self.walls):
                return n, p, False
        return by_dist[0], self.points[by_dist[0]], True

    def set_destination(self, xy, label: str = "your pin") -> dict:
        found = self.pin_node(xy)
        if found is None:
            return {"ok": False, "reason": "no map"}
        node, point, moved = found
        if self.entrance in self.points and shortest_path(self.graph, node, self.entrance) is None:
            return {"ok": False, "reason": "that spot is not connected to the rest of the floor"}
        self.goal, self.goal_xy, self.goal_label, self.goal_kind = node, point, label, "pin"
        return {"ok": True, "x": round(point[0], 2), "y": round(point[1], 2), "moved": moved,
                "label": label, "kind": "pin"}

    def clear_destination(self) -> dict:
        self.goal, self.goal_label, self.goal_kind = self.entrance, self.entrance_label, "entrance"
        self.goal_xy = self.points.get(self.entrance) if self.entrance is not None else None
        return {"ok": True, "label": self.goal_label, "kind": "entrance"}

    def destination(self) -> dict:
        xy = self.goal_xy
        return {"label": self.goal_label, "kind": self.goal_kind,
                "x": round(xy[0], 2) if xy else None, "y": round(xy[1], 2) if xy else None}

    def at_destination(self, xy, node: str, within: float) -> bool:
        return node == self.goal and self.goal_xy is not None and math.dist(xy, self.goal_xy) <= within

    def label(self, pid: str | None) -> str:
        if pid is None:
            return "unknown"
        return self.goal_label if pid == self.goal else self.labels.get(pid, pid)


def _first_do(route: Route, heading: float | None) -> str:
    if heading is None:
        return "stop and hold the phone facing forward while heading is determined"
    turn = turn_between(heading, route.bearings[0])
    if abs(turn) <= TURN_DEG:
        return "go straight"
    if abs(turn) >= AROUND_DEG:
        return "turn around"
    return f"turn {'right' if turn > 0 else 'left'} to your {clock(turn)} o'clock"


def _legs(route: Route, heading: float | None, stride: float = STRIDE_M) -> list[dict]:
    """Walking legs for the agent. The first starts where the person faces (heading); each later
    one starts with the turn that ends the leg before it."""
    legs, s0, begin = [], 0.0, None
    for m in route.maneuvers:
        legs.append({"do": _first_do(route, heading) if begin is None else begin.say,
                     "meters": round(m.s - s0, 1), "steps": max(1, round((m.s - s0) / stride))})
        s0, begin = m.s, m
    return legs


def describe_route(emap: EntranceMap, place: str | None, yaw_deg: float | None = None,
                   position: dict | None = None, heading_deg: float | None = None,
                   stride: float = STRIDE_M) -> dict:
    """The whole path from `place` to the destination as walking legs, for the agent to explain.

    The same Route the guide follows, so the numbers match the spoken guidance."""
    pts = emap.points
    if place is None or place not in pts:
        return {"ok": False, "reason": "position not known yet; hold the phone steady"}
    xy, node = start_point(emap, place, position)
    if emap.at_destination(xy, node, WAYPOINT_M):
        return {"ok": True, "at_destination": True, "total_m": 0.0, "legs": []}
    route = plan(emap, xy, node)
    if route is None:
        return {"ok": False, "reason": f"no route to {emap.goal_label} from here"}
    heading = None if heading_deg is None else float(heading_deg)
    M = emap.frames.get(node)
    if heading is None and yaw_deg is not None and M is not None:
        heading = mesh_heading_deg(M, yaw_deg)
    next_turn = route.turns[0] if route.turns else route.maneuvers[-1]
    return {"ok": True, "destination": emap.goal_label, "total_m": round(route.length, 1),
            "legs": _legs(route, heading, stride), "heading_known": heading is not None, "stride_m": round(stride, 2),
            "total_steps": max(1, round(route.length / stride)),
            "next_turn": emap.goal_label if next_turn.say == "arrive" else next_turn.say,
            "next_turn_in_m": round(next_turn.s, 1)}


def route_sentence(r: dict) -> str:
    """Compact plain-text form of describe_route() for the agent's context line."""
    if not r.get("ok"):
        return r.get("reason", "no route")
    if r.get("at_destination"):
        return f"already at {r.get('destination', 'the destination')}"
    steps = "; then ".join(f"{leg['do']}, about {leg.get('steps', leg['meters'])} steps" for leg in r["legs"])
    total = r.get("total_steps") or max(1, round(r["total_m"] / STRIDE_M))
    return f"about {total} steps to {r['destination']}: {steps}"


def _side(turn: float) -> str:
    return "right" if turn > 0 else "left"


class EntranceGuide:
    """Spoken guidance to the entrance, one instruction at a time, said at the moments that matter:
    the heading for the first leg, 5 m and then 1.5 m before each turn, the turn itself, arrival."""

    def __init__(self, emap: EntranceMap, repeat_s: float = REPEAT_S):
        self.map = emap
        self.repeat_s = repeat_s
        self.active = False
        self.route: Route | None = None
        self.progress: Progress | None = None
        self.k = 0  # index of the maneuver being guided to
        # align: face the leg we are on; walk: going straight; turn: making the turn at maneuver k
        self.phase = "align"
        self.said: set[tuple[int, str]] = set()  # distance prompts already spoken, per maneuver
        self.turn_from: float | None = None  # heading when "turn now" was said
        self.turn_t = 0.0
        self.last_text: tuple | None = None  # (instruction, maneuver) last spoken
        self.last_t = -math.inf
        self.reminded = False
        self.status: dict = {"active": False}

    def start(self, start_place: str | None, position: dict | None = None) -> dict:
        if self.map.goal not in self.map.points:
            return {"ok": False, "reason": "no entrance configured on this floor"}
        self.active = True
        self.last_text, self.last_t, self.reminded = None, -math.inf, False
        self.route, self.progress = None, None
        out = {"ok": True, "destination": self.map.goal_label}
        if start_place is not None and start_place in self.map.points:
            self._plan(*start_point(self.map, start_place, position))
        self.status = {"active": True}
        if self.route is not None:
            out.update(waypoints=len(self.route.turns), distance_m=round(self.route.length, 1))
        else:
            out["note"] = "position not known yet; tell them to hold the phone steady and you'll find the path"
        return out

    def stop(self) -> dict:
        self.active, self.route, self.progress, self.k = False, None, None, 0
        self.status = {"active": False}
        return {"ok": True}

    def _plan(self, xy, node: str) -> bool:
        self.route = plan(self.map, xy, node)
        self.progress = Progress(self.route) if self.route is not None else None
        self.k, self.phase, self.said = 0, "align", set()
        return self.route is not None

    def update(self, t: float, place: str | None, state: str, yaw_deg: float | None,
               position: dict | None, heading_deg: float | None = None,
               steps: int | None = None, fresh: bool = True) -> str | None:
        """Spoken instruction for this frame, or None (nothing new to say). heading_deg, when given,
        is the fused mesh-frame heading (server/heading.py) and wins over yaw_deg. steps is the
        phone's cumulative step count (pedometer), used between position fixes. fresh=False when
        position is not a new fix (an IMU sample between frames)."""
        if not self.active:
            return None
        text = self._instruction(t, place, state, yaw_deg, position, heading_deg, steps, fresh)
        if text is None:
            return None
        key = (text, self.k)  # same words toward a new maneuver are a new instruction
        if text.startswith("Hold the phone steady") and self.last_text == key:
            return None  # say the pause once
        if self.last_text == key:
            if self.reminded or t - self.last_t < self.repeat_s:
                return None
            self.reminded = True  # one reminder when nothing has changed
        else:
            self.last_text, self.reminded = key, False
        self.last_t = t
        if text.startswith("You're at "):
            self.stop()
            self.status = {"active": False, "arrived": True, "destination": self.map.goal_label}
        return text

    def _instruction(self, t, place, state, yaw_deg, position, heading_deg, steps, fresh=True) -> str | None:
        pts = self.map.points
        if state == "unknown" or place is None or place not in pts:
            self.status = {**self.status, "paused": True}
            return "Hold the phone steady and I'll find the path."
        xy, node = start_point(self.map, place, position)
        if self.map.at_destination(xy, node, ARRIVE_M):
            return self.arrived_text
        if self.route is None and not self._plan(xy, node):
            return f"I can't find a route to {self.map.goal_label} from here."
        trusted = _trusted(position)
        sigma = float(position["sigma_m"]) if trusted else None
        self.progress.update(t, xy if trusted else None, sigma, steps, fix=fresh)
        prefix = ""
        if self.progress.off_route():
            if not self._plan(xy, node):
                return f"Let me find a new way. I can't find a route to {self.map.goal_label} from here."
            prefix = "Let me find a new way. "
        s = self.progress.s
        if self.route.length - s <= ARRIVE_M:
            return self.arrived_text
        M = self.map.frames.get(node)
        if heading_deg is not None:
            heading = float(heading_deg)
        elif yaw_deg is None or M is None:
            heading = None
        else:
            heading = mesh_heading_deg(M, yaw_deg)
        text = self._step(t, s, heading)
        self.status = self._status(s, heading)
        return None if text is None else prefix + text

    def _target(self, s: float) -> float:
        """Bearing the person should face: the leg they are on, or the leg after a turn."""
        m = self.route.maneuvers[self.k]
        if self.phase == "turn":
            return self.route.bearings[m.seg]
        return self.route.bearings[self.route.segment_at(s)]

    def _step(self, t: float, s: float, heading: float | None) -> str | None:
        """The instruction for the current phase, or None when there is nothing new to say."""
        m = self.route.maneuvers[self.k]
        if self.phase == "walk":
            if m.say == "arrive":
                return None
            d = m.s - s
            if d <= NOW_M and (self.k, "now") not in self.said:
                self.said.add((self.k, "now"))
                self.phase, self.turn_from, self.turn_t = "turn", heading, t
                return f"{m.say[0].upper()}{m.say[1:]} now."
            if d <= FIVE_M and (self.k, "five") not in self.said:
                self.said.add((self.k, "five"))
                return f"In about {steps_text(d, self.stride)}, {m.say}."
            return None
        if heading is None:
            return "Stop. Hold the phone facing forward while I determine your heading."
        turn = turn_between(heading, self._target(s))
        turned = TURN_DEG + 1  # unknown start heading: judge the turn as done
        if self.phase == "turn" and self.turn_from is not None:
            turned = abs(turn_between(self.turn_from, heading))
            if turned < TURN_DEG and t - self.turn_t < TURN_WAIT_S:
                return None  # give the turn time before judging it
        if abs(turn) > TURN_DEG:
            if self.phase == "align" or turned < TURN_DEG:
                return f"Turn {_side(turn)}, to your {clock(turn)} o'clock."
            return f"Turn a bit more to your {_side(turn)}, to your {clock(turn)} o'clock."
        if self.phase == "turn":
            self.k += 1  # the turn is done: on to the next maneuver
        self.phase = "walk"
        return self._leg_text(s)

    def _leg_text(self, s: float) -> str:
        m = self.route.maneuvers[self.k]
        if m.s - s <= FIVE_M + 1.0:
            self.said.add((self.k, "five"))  # the leg text already says how close the turn is
        dist = steps_text(m.s - s, self.stride)
        if m.say == "arrive":
            return f"Go straight about {dist} to {self.map.goal_label}."
        return f"Go straight about {dist}, then {m.say}."

    @property
    def arrived_text(self) -> str:
        return f"You're at {self.map.goal_label}."

    def replan(self, start_place: str | None, position: dict | None) -> dict:
        """The destination changed: start over toward it from where the person is now."""
        self.stop()
        return self.start(start_place, position)

    @property
    def stride(self) -> float:
        """Metres per step: the pedometer stride calibrated against position fixes, else the default."""
        return self.progress.stride if self.progress is not None else STRIDE_M

    def _status(self, s: float, heading: float | None) -> dict:
        m = self.route.maneuvers[self.k]
        out = {"active": True, "next": self.map.goal_label if m.say == "arrive" else m.say,
               "next_is_destination": m.say == "arrive", "destination": self.map.goal_label,
               "next_m": round(max(0.0, m.s - s), 1), "remaining_m": round(max(0.0, self.route.length - s), 1),
               "waypoints_left": len(self.route.maneuvers) - self.k,
               "next_steps": max(0, round(max(0.0, m.s - s) / self.stride)),
               "remaining_steps": max(0, round(max(0.0, self.route.length - s) / self.stride)),
               "stride_m": round(self.stride, 2)}
        if heading is not None:
            out.update(heading_deg=round(heading), turn_deg=round(turn_between(heading, self._target(s))))
        return out
