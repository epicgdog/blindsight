"""HackPrinceton venue: find a room by number or by what it is for, and route there across levels.

Data: server/venue/venue.json (rooms, walk graph per level, stair and elevator links; built by
tools/venue_graph.py from the HackPrinceton maps) and server/venue/align.json (per level: image
pixels <-> map metres, the Matterport frame localization uses).

  search("judges room")  -> A98, A97 (nearest first when the start is known)
  search("a ninety seven") -> A97
  route(to="A98", start=Start(level="1", px=(515, 440)))
      -> legs per level (pixels and metres), spoken steps, total metres

Routes follow the traced corridors, so they exist on every level, scanned or not.
"""

from __future__ import annotations

import difflib
import heapq
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.staticfiles import StaticFiles

DIR = Path(__file__).resolve().parent / "venue"
MARKED = Path(__file__).resolve().parents[1] / "data" / "venue" / "marked.json"  # places marked on site
MARK_KINDS = {
    "restroom": ("Restroom", ["bathroom", "toilet", "restroom", "washroom", "mens room", "womens room"]),
    "water": ("Water fountain", ["water", "water fountain", "drinking fountain", "fountain", "bottle filler",
                                 "refill", "drink"]),
}
STAIRS_M = 8.0  # walking cost of one flight of stairs
STAIRS_PENALTY_M = 40.0  # the elevator is taken unless it adds more than this (blind user)
ELEVATOR_M = 15.0  # waiting and riding, per level
TURN_DEG = 35.0  # a direction change above this is a spoken turn
SCAN_FLOOR_LEVEL = {0: "1", 2: "2"}  # our scan floor index -> venue level
LEVEL_Z_SPLIT_M = 3.4  # camera height above this (map z) is level 2, below is level 1
SNAP_MAX_M = 8.0  # a position further than this from any walkable floor is left as is
NODE_DETOUR_M = 12.0  # a route may start at a node this much further away to avoid crossing a hole

_UNITS = {"zero": 0, "oh": 0, "o": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
          "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_FILLER = {"room", "rooms", "the", "to", "take", "me", "go", "where", "is", "are", "find", "number", "please",
           "nearest", "closest", "a", "an", "my", "i", "need", "want", "get", "how", "do", "for", "what", "of",
           "in", "at", "show", "navigate", "directions", "way"}


@dataclass
class Start:
    level: str
    px: tuple[float, float]
    snapped_m: float = 0.0  # how far the position was moved onto walkable floor


def spoken_numbers(text: str) -> str:
    """'two oh one' -> '201', 'one ninety eight' -> '198', 'a ninety seven' -> 'a 97',
    'two hundred one' -> '201'. Digits already in the text are kept."""
    out: list[str] = []
    group: list[int] = []  # digits of the number being read
    words = re.findall(r"[a-z]+|\d+|[^\sa-z\d]", text.lower())

    def flush():
        if group:
            out.append("".join(str(d) for d in group))
            group.clear()

    i = 0
    while i < len(words):
        w = words[i]
        if w in _UNITS and not (w in ("o", "oh") and not group):
            group.append(_UNITS[w])
        elif w in _TEENS:
            group.extend(divmod(_TEENS[w], 10))
        elif w in _TENS:
            nxt = words[i + 1] if i + 1 < len(words) else ""
            if nxt in _UNITS and _UNITS[nxt] > 0:
                group.extend((_TENS[w] // 10, _UNITS[nxt]))
                i += 1
            else:
                group.extend((_TENS[w] // 10, 0))
        elif w == "hundred" and len(group) == 1:
            # 'two hundred one' -> 2, then the rest as two digits
            rest = words[i + 1:i + 3]
            if rest and rest[0] in _TENS:
                tens = _TENS[rest[0]] // 10
                unit = _UNITS.get(rest[1], 0) if len(rest) > 1 and rest[1] in _UNITS else 0
                group.extend((tens, unit))
                i += 1 + (1 if len(rest) > 1 and rest[1] in _UNITS else 0)
            elif rest and rest[0] in _TEENS:
                group.extend(divmod(_TEENS[rest[0]], 10))
                i += 1
            elif rest and rest[0] in _UNITS:
                group.extend((0, _UNITS[rest[0]]))
                i += 1
            else:
                group.extend((0, 0))
        else:
            flush()
            out.append(w)
        i += 1
    flush()
    return " ".join(out)


def _norm(text: str) -> str:
    t = spoken_numbers(text.replace("’", "'"))
    t = re.sub(r"[^a-z0-9' ]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


class Venue:
    def __init__(self, data: dict, align: dict, marked_path: Path | None = None, walk_dir: Path | None = None):
        self.walk_dir = walk_dir  # walk_<level>.png masks (tools/venue_walkable.py); None: no snapping
        self._walk: dict[str, Any] = {}
        self.levels = {lv["id"]: lv for lv in data["levels"]}
        self.rooms = {r["id"]: r for r in data["rooms"]}
        self.align = align
        self.nodes = {lvl: {k: tuple(v) for k, v in nodes.items()} for lvl, nodes in data["nodes"].items()}
        self.links = data["links"]
        self.graph: dict[tuple[str, str], list[tuple[tuple[str, str], float, dict | None]]] = {}
        for lvl, chains in data["corridors"].items():
            for chain in chains:
                for a, b in zip(chain, chain[1:]):
                    self._edge((lvl, a), (lvl, b), self.px_dist(lvl, self.nodes[lvl][a], self.nodes[lvl][b]), None)
        order = {lv["id"]: lv["order"] for lv in data["levels"]}
        for link in self.links:
            lv = sorted(link["nodes"], key=lambda k: order[k])
            for lo, hi in zip(lv, lv[1:]):
                if abs(order[hi] - order[lo]) != 1:
                    continue
                cost = ELEVATOR_M if link["kind"] == "elevator" else STAIRS_M + STAIRS_PENALTY_M
                self._edge((lo, link["nodes"][lo]), (hi, link["nodes"][hi]), cost, link)
        self.marked_path = marked_path
        if marked_path is not None and marked_path.exists():
            try:
                for r in json.loads(marked_path.read_text()):
                    if r.get("level") in self.nodes and r.get("door"):
                        r["join"] = self._attach(r["level"], tuple(r["door"]), r["id"])
                        self.rooms[r["id"]] = r
            except (OSError, ValueError, KeyError):
                pass

    @classmethod
    def load(cls, directory: Path = DIR, marked_path: Path | None = MARKED) -> "Venue":
        return cls(json.loads((directory / "venue.json").read_text()), json.loads((directory / "align.json").read_text()),
                   marked_path, walk_dir=directory)

    _shared: "Venue | None" = None

    @classmethod
    def shared(cls) -> "Venue":
        """One instance for the API and the voice agent, so a place marked from the phone is
        found by voice at once."""
        if cls._shared is None:
            cls._shared = cls.load()
        return cls._shared

    # -- places marked on site ("Mark here") -------------------------------------
    def mark(self, kind: str, start: Start, name: str | None = None) -> dict:
        """Add a restroom or water fountain where the person stands; saved for later sessions."""
        if kind not in MARK_KINDS:
            raise ValueError(f"kind must be one of {sorted(MARK_KINDS)}")
        title, aliases = MARK_KINDS[kind]
        n = 1 + sum(1 for k in self.rooms if k.startswith(f"m-{kind}-"))
        rid = f"m-{kind}-{n}"
        while rid in self.rooms:
            n += 1
            rid = f"m-{kind}-{n}"
        px = (round(float(start.px[0]), 1), round(float(start.px[1]), 1))
        join = self._attach(start.level, px, rid)
        title = (name or title)[:60]
        room = {"id": rid, "level": start.level, "building": "", "label": px, "door": px, "join": join,
                "name": title, "spoken": f"the {title.lower()}", "roles": [kind], "aliases": aliases,
                "marked": True, "kind": kind, "note": "Marked on site with the phone."}
        self.rooms[rid] = room
        self._save_marked()
        return self.room_info(rid)

    def unmark(self, rid: str) -> bool:
        if not self.rooms.get(rid, {}).get("marked"):
            return False
        del self.rooms[rid]
        self._save_marked()
        return True

    def _save_marked(self) -> None:
        if self.marked_path is None:
            return
        self.marked_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [r for r in self.rooms.values() if r.get("marked")]
        self.marked_path.write_text(json.dumps(rows, indent=1))

    def _attach(self, level: str, px: tuple[float, float], name: str) -> str:
        """Join a point to the walk graph: split the nearest corridor segment of the level at the
        point's projection, so a place mid-corridor is reached there, not via a far corner."""
        best = None
        for a, nbrs in self.graph.items():
            if a[0] != level:
                continue
            for b, _, link in nbrs:
                if link is not None or b[0] != level or b <= a:
                    continue
                pa, pb = self.nodes[level][a[1]], self.nodes[level][b[1]]
                dx, dy = pb[0] - pa[0], pb[1] - pa[1]
                L2 = dx * dx + dy * dy
                t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px[0] - pa[0]) * dx + (px[1] - pa[1]) * dy) / L2))
                q = (pa[0] + t * dx, pa[1] + t * dy)
                d = math.dist(q, px)
                if best is None or d < best[0]:
                    best = (d, a, b, q)
        if best is None:
            return self.nearest_node(Start(level, px))[1]
        node = f"@{name}"
        _, a, b, q = best
        self.nodes[level][node] = (round(q[0], 1), round(q[1], 1))
        self.graph[a] = [e for e in self.graph[a] if e[0] != b]
        self.graph[b] = [e for e in self.graph[b] if e[0] != a]
        self._edge(a, (level, node), self.px_dist(level, self.nodes[level][a[1]], q), None)
        self._edge((level, node), b, self.px_dist(level, q, self.nodes[level][b[1]]), None)
        return node

    def _edge(self, a, b, w, link):
        self.graph.setdefault(a, []).append((b, w, link))
        self.graph.setdefault(b, []).append((a, w, link))

    # -- coordinates -----------------------------------------------------------
    def scale(self, level: str) -> float:
        return float(self.align[level]["scale"])

    def px_dist(self, level: str, a, b) -> float:
        return math.dist(a, b) / self.scale(level)

    def to_px(self, level: str, x: float, y: float) -> tuple[float, float]:
        t = self.align[level]
        c, s = math.cos(math.radians(t["rot"])), math.sin(math.radians(t["rot"]))
        return (t["scale"] * (c * x - s * y) + t["tx"], -t["scale"] * (s * x + c * y) + t["ty"])

    def to_map(self, level: str, u: float, v: float) -> tuple[float, float]:
        t = self.align[level]
        c, s = math.cos(math.radians(t["rot"])), math.sin(math.radians(t["rot"]))
        a, b = (u - t["tx"]) / t["scale"], -(v - t["ty"]) / t["scale"]
        return (c * a + s * b, -s * a + c * b)

    # -- where a person can stand ------------------------------------------------
    def walk_mask(self, level: str):
        """Bool array (plan pixels) of floor a person can stand on: corridors and event rooms,
        minus holes (Level 2 atrium voids, open-to-below) found in the 3D scan or marked with an
        X on the plan. None when there is no mask for the level."""
        if level not in self._walk:
            mask = None
            path = self.walk_dir / f"walk_{level}.png" if self.walk_dir is not None else None
            if path is not None and path.exists():
                import numpy as np
                from PIL import Image
                mask = np.asarray(Image.open(path).convert("L")) > 127
            self._walk[level] = mask
        return self._walk[level]

    def walkable(self, level: str, px) -> bool:
        mask = self.walk_mask(level)
        if mask is None:
            return True
        u, v = int(round(px[0])), int(round(px[1]))
        return 0 <= v < mask.shape[0] and 0 <= u < mask.shape[1] and bool(mask[v, u])

    def snap(self, level: str, px, max_m: float = SNAP_MAX_M) -> tuple[tuple[float, float], float]:
        """The nearest walkable pixel to px (within max_m), and how far it moved in metres. A point
        on walkable floor, or with nothing walkable near it, is returned unchanged."""
        mask = self.walk_mask(level)
        if mask is None or self.walkable(level, px):
            return (float(px[0]), float(px[1])), 0.0
        import numpy as np
        r = int(math.ceil(max_m * self.scale(level)))
        u, v = int(round(px[0])), int(round(px[1]))
        u0, v0 = max(0, u - r), max(0, v - r)
        ys, xs = np.nonzero(mask[v0:v + r + 1, u0:u + r + 1])
        if not len(xs):
            return (float(px[0]), float(px[1])), 0.0
        d2 = (xs + u0 - px[0]) ** 2 + (ys + v0 - px[1]) ** 2
        i = int(np.argmin(d2))
        out = (float(xs[i] + u0), float(ys[i] + v0))
        return out, round(self.px_dist(level, px, out), 2)

    def clear_line(self, level: str, a, b, end_px: float = 4.0) -> bool:
        """True when the straight line a -> b stays on walkable floor (ends excepted, since doors
        and graph nodes can sit on a wall line)."""
        mask = self.walk_mask(level)
        d = math.dist(a, b)
        if mask is None or d <= 2 * end_px:
            return True
        n = int(d // 2)
        good = total = 0
        for i in range(n + 1):
            t = i / n
            if d * t < end_px or d * (1 - t) < end_px:
                continue
            total += 1
            good += self.walkable(level, (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        return total == 0 or good >= 0.85 * total

    def level_of(self, z: float | None = None, scan_floor: int | None = None) -> str | None:
        if z is not None and math.isfinite(z):
            return "2" if z > LEVEL_Z_SPLIT_M else "1"
        return SCAN_FLOOR_LEVEL.get(scan_floor) if scan_floor is not None else None

    def start_from_position(self, position: dict | None, scan_floor: int | None = None) -> Start | None:
        """A tracked map position (metres, z up) as a venue start."""
        if not position or position.get("x") is None or position.get("y") is None:
            return None
        level = self.level_of(position.get("z"), scan_floor)
        if level is None:
            return None
        # a position in a hole, a wall or an office is moved to the nearest floor a person can stand on
        px, moved = self.snap(level, self.to_px(level, float(position["x"]), float(position["y"])))
        return Start(level, px, moved)

    # -- search ----------------------------------------------------------------
    def search(self, query: str, start: Start | None = None, limit: int = 5) -> list[dict]:
        q = _norm(query)
        if not q:
            return []
        compact = q.replace(" ", "")
        words = [w for w in q.split() if w not in _FILLER or w in ("a", "b")]
        scored: dict[str, float] = {}
        # 1. room number: "a97", "a 97", "201", "room 198"
        for rid, room in self.rooms.items():
            num = rid.lower()
            if not re.fullmatch(r"[a-z]?\d+[a-z]?", num):
                continue
            tokens = re.findall(r"\b[a-z]?\s?\d+[a-z]?\b", q)
            if any(t.replace(" ", "") == num for t in tokens) or compact.endswith(num) and compact.replace(num, "").strip() in (
                    "", "room", "roomnumber"):
                scored[rid] = 100.0
        if not scored:
            # 2. what the room is for: aliases and roles as whole words or phrases
            for rid, room in self.rooms.items():
                best = 0.0
                for phrase in [*room.get("aliases", []), *room.get("roles", []), room["name"]]:
                    p = _norm(phrase)
                    if p and re.search(rf"\b{re.escape(p)}\b", q):
                        best = max(best, 50.0 + len(p))  # longer phrase = more specific
                if best:
                    scored[rid] = best
        if not scored and words:
            # 3. typos: close match on single alias words
            for rid, room in self.rooms.items():
                for phrase in [*room.get("aliases", []), *room.get("roles", []), room["name"]]:
                    for pw in _norm(phrase).split():
                        if len(pw) < 4 or pw in _FILLER:
                            continue
                        for w in words:
                            if len(w) >= 4 and difflib.SequenceMatcher(None, w, pw).ratio() >= 0.8:
                                scored[rid] = max(scored.get(rid, 0.0), 20.0)
        if not scored:
            return []
        top = max(scored.values())
        hits = [rid for rid, s in scored.items() if s >= top]
        out = []
        for rid in hits:
            r = self.room_info(rid)
            if start is not None:
                rt = self.route(rid, start)
                r["distance_m"] = rt["total_m"] if rt.get("ok") else None
            out.append(r)
        if start is not None:
            out.sort(key=lambda r: (r["distance_m"] is None, r["distance_m"] or 0.0))
        return out[:limit]

    def room_info(self, rid: str) -> dict:
        r = self.rooms[rid]
        lv = self.levels[r["level"]]
        name = r["name"]
        spoken = r.get("spoken") or (name[0].lower() + name[1:] if re.search(r"\d", name) else f"the {name}")
        out = {"id": rid, "name": r["name"], "spoken": spoken, "level": r["level"], "level_name": lv["name"],
               "building": r["building"], "roles": r.get("roles", []), "label_px": list(r["label"]),
               "door_px": list(r["door"])}
        if r.get("note"):
            out["note"] = r["note"]
        if r.get("approximate"):
            out["approximate"] = True
        if r.get("marked"):
            out["marked"] = True
        return out

    # -- routing ---------------------------------------------------------------
    def nearest_node(self, start: Start) -> tuple[str, str]:
        """The nearest graph node reachable in a straight line over walkable floor (so a route never
        starts by crossing a hole or a wall), else the nearest node."""
        nodes = self.nodes[start.level]
        order = sorted(nodes, key=lambda n: math.dist(nodes[n], start.px))
        limit = math.dist(nodes[order[0]], start.px) + NODE_DETOUR_M * self.scale(start.level)
        for name in order:
            if math.dist(nodes[name], start.px) > limit:
                break
            if self.clear_line(start.level, start.px, nodes[name]):
                return (start.level, name)
        return (start.level, order[0])

    def route(self, to: str, start: Start, prefer: str | None = None, heading_deg: float | None = None) -> dict:
        """Shortest walk from start to the room's door. prefer: 'stairs' or 'elevator' (else the
        elevator unless it adds more than STAIRS_PENALTY_M). heading_deg: the way the person faces
        (map frame, clockwise from north), which makes the first step a turn relative to them."""
        if to not in self.rooms:
            return {"ok": False, "reason": f"unknown room {to}"}
        if start.level not in self.nodes:
            return {"ok": False, "reason": f"unknown level {start.level}"}
        room = self.rooms[to]
        goal = (room["level"], room["join"])
        src = self.nearest_node(start)
        dist, prev = {src: 0.0}, {}
        heap = [(0.0, src)]
        while heap:
            d, cur = heapq.heappop(heap)
            if cur == goal:
                break
            if d > dist.get(cur, math.inf):
                continue
            for nxt, w, link in self.graph.get(cur, []):
                if link is not None and prefer:
                    if prefer == "stairs" and link["kind"] == "elevator":
                        continue
                    if prefer == "elevator" and link["kind"] == "stairs":
                        continue
                    if prefer == "stairs":
                        w = STAIRS_M
                nd = d + w
                if nd < dist.get(nxt, math.inf):
                    dist[nxt], prev[nxt] = nd, (cur, link)
                    heapq.heappush(heap, (nd, nxt))
        if goal not in dist:
            return {"ok": False, "reason": f"no route to {to}"}
        chain, links = [goal], []
        while chain[-1] != src:
            p, link = prev[chain[-1]]
            if link is not None:
                links.append((p, chain[-1], link))
            chain.append(p)
        chain.reverse()
        links.reverse()
        # legs: one per level, start point first, door last
        legs: list[dict] = []
        cur_level, pts = start.level, [tuple(start.px)]
        for lvl, name in chain:
            if lvl != cur_level:
                legs.append({"level": cur_level, "px": pts})
                cur_level, pts = lvl, []
            pts.append(self.nodes[lvl][name])
        join = self.nodes[room["level"]][room["join"]]
        door_hop = math.dist(join, room["door"]) > 0.5  # an atrium's "door" is its centre: no hop
        pts.append(tuple(room["door"]))
        legs.append({"level": cur_level, "px": pts})
        for leg in legs:
            leg["px"] = _dedupe(leg["px"])
            leg["m"] = [list(map(lambda v: round(v, 2), self.to_map(leg["level"], *p))) for p in leg["px"]]
            leg["length_m"] = round(sum(self.px_dist(leg["level"], a, b) for a, b in zip(leg["px"], leg["px"][1:])), 1)
            leg["px"] = [[round(u, 1), round(v, 1)] for u, v in leg["px"]]
        walk = sum(leg["length_m"] for leg in legs)
        transfers = [{"from_level": a[0], "to_level": b[0], "kind": link["kind"], "name": link["name"]}
                     for a, b, link in links]
        out = {"ok": True, "to": self.room_info(to), "from_level": start.level, "legs": legs, "door_hop": door_hop,
               "transfers": transfers, "total_m": round(walk + STAIRS_M * sum(t["kind"] == "stairs" for t in transfers), 1)}
        out["steps"] = self.steps(out, heading_deg)
        return out

    def steps(self, route: dict, heading_deg: float | None = None) -> list[str]:
        """Spoken step list: walk distances, turns, level changes, which side the door is on.
        With heading_deg the first step starts with the turn from where the person faces."""
        out: list[str] = []
        legs, transfers = route["legs"], route["transfers"]
        order = {k: v["order"] for k, v in self.levels.items()}
        for i, leg in enumerate(legs):
            pts = leg["px"]
            if i == len(legs) - 1 and route.get("door_hop") and len(pts) > 2:
                pts = pts[:-1]  # the last hop is corridor -> door: said as "the door on your left/right"
            segs = _segments(pts, self.scale(leg["level"]))
            for j, (length, bearing) in enumerate(segs):
                if j == 0 and i == 0 and heading_deg is not None and math.isfinite(heading_deg):
                    # image up is map north (align rot is 0), so bearings compare directly
                    out.append(_first_step(_turn(heading_deg - self.align[leg["level"]]["rot"], bearing), length))
                elif j == 0:
                    lead = "Walk" if i == 0 else "From there, walk"
                    out.append(f"{lead} about {_m(length)}.")
                else:
                    turn = _turn(segs[j - 1][1], bearing)
                    out.append(f"Turn {'right' if turn > 0 else 'left'}{' slightly' if abs(turn) < 60 else ''}, "
                               f"then walk about {_m(length)}.")
            if i < len(transfers):
                t = transfers[i]
                up = order[t["to_level"]] > order[t["from_level"]]
                lv = self.levels[t["to_level"]]["spoken"]
                verb = "Take" if t["kind"] == "elevator" else "Use"
                out.append(f"{verb} {t['name']} {'up' if up else 'down'} to {lv}.")
        room = route["to"]
        name = room["spoken"]
        if room.get("approximate"):
            # door not traced on the plan: no left or right claim
            out.append(f"{name} is close to here, but its door is not on the map; ask someone nearby.")
            return out
        name = name[0].upper() + name[1:]
        side = _door_side(legs[-1]["px"]) if route.get("door_hop") else None
        if not route.get("door_hop"):
            out.append(f"You are at {route['to']['spoken']}.")
        elif side == "ahead":
            out.append(f"{name} is straight ahead.")
        elif side:
            thing = "" if self.rooms.get(room["id"], {}).get("kind") == "water" else "the door "
            out.append(f"{name} is {thing}on your {side}.")
        else:
            out.append(f"You have reached {name}.")
        return out

    # -- for the phone -----------------------------------------------------------
    def describe(self) -> dict:
        return {"levels": [{**lv, "image_url": f"/venue/{lv['image']}", "align": self.align.get(lv["id"])}
                           for lv in sorted(self.levels.values(), key=lambda v: -v["order"])],
                "rooms": [self.room_info(rid) for rid in self.rooms]}


def _dedupe(pts):
    out = []
    for p in pts:
        if not out or math.dist(out[-1], p) > 0.5:
            out.append(tuple(p))
    return out


def _bearing(a, b) -> float:
    """Image bearing in degrees, clockwise from up (north) since image y grows down."""
    return math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) % 360


def _turn(frm: float, to: float) -> float:
    return (to - frm + 540) % 360 - 180


def _segments(pts, scale: float) -> list[tuple[float, float]]:
    """Straight runs (metres, bearing), merging steps that bend less than TURN_DEG."""
    segs: list[list[float]] = []
    for a, b in zip(pts, pts[1:]):
        length = math.dist(a, b) / scale
        if length < 0.3:
            continue
        br = _bearing(a, b)
        if segs and abs(_turn(segs[-1][1], br)) < TURN_DEG:
            segs[-1][0] += length
        else:
            segs.append([length, br])
    # A jog under 1.2 m (a corridor corner, a step to the stair door) is not worth a spoken turn.
    if len(segs) > 1:
        segs = [g for g in segs if g[0] >= 1.2] or segs[:1]
    return [(l, b) for l, b in segs]


def _door_side(pts, min_px: float = 9.0) -> str | None:
    """Left or right of the last corridor run, for the final step to the door. The run is the
    last one at least min_px long (about 1.2 m): shorter jogs are not spoken, so they must not
    decide the side either."""
    if len(pts) < 3:
        return None
    b, c = pts[-2], pts[-1]
    a = next((p for p in reversed(pts[:-2]) if math.dist(p, b) >= min_px), None)
    if a is None:
        return None
    v1 = (b[0] - a[0], b[1] - a[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    if math.hypot(*v2) < 1e-6 or math.hypot(*v1) < 1e-6:
        return None
    cross = v1[0] * v2[1] - v1[1] * v2[0]  # image y down: positive is clockwise, i.e. to the right
    ang = abs(_turn(_bearing(a, b), _bearing(b, c)))
    if ang < 30:
        return "ahead"
    return "right" if cross > 0 else "left"


def _first_step(turn: float, length: float) -> str:
    a = abs(turn)
    side = "right" if turn > 0 else "left"
    if a <= 30:
        return f"Walk straight ahead about {_m(length)}."
    if a >= 150:
        return f"Turn around, then walk about {_m(length)}."
    return f"Turn {'slightly ' if a < 60 else ''}{side}, then walk about {_m(length)}."


def _m(meters: float) -> str:
    n = max(1, round(meters))
    return f"{n} meter{'s' if n != 1 else ''}"


def venue_router(get_venue, get_start=None) -> APIRouter:
    """get_venue() -> Venue; get_start() -> Start of the person now, or None."""
    router = APIRouter()

    def start_from(level: str | None, u: float | None, v: float | None, room: str | None) -> Start | None:
        venue = get_venue()
        if room:
            r = venue.rooms.get(room)
            if r is None:
                raise HTTPException(404, f"unknown room {room}")
            return Start(r["level"], tuple(r["door"]))
        if level is not None and u is not None and v is not None:
            if level not in venue.levels:
                raise HTTPException(422, "unknown level")
            return Start(level, (u, v))
        return get_start() if get_start else None

    @router.get("/api/venue")
    async def venue_info():
        return get_venue().describe()

    @router.get("/api/venue/search")
    async def venue_search(q: str, level: str | None = None, u: float | None = None, v: float | None = None,
                           from_room: str | None = None):
        start = start_from(level, u, v, from_room)
        assumed = start is None
        if assumed:  # position unknown: rank from check-in (117), the usual starting point
            start = start_from(None, None, None, "117")
        return {"query": q, "start": {"level": start.level, "px": list(start.px)}, "start_assumed": assumed,
                "results": get_venue().search(q, start)}

    @router.post("/api/venue/mark")
    async def venue_mark(body: dict):
        """{"kind": "restroom" | "water", "name"?: str}: mark it where the person stands now."""
        start = get_start() if get_start else None
        if start is None:
            raise HTTPException(409, "Position unknown: hold the phone up until the map shows you, then mark")
        try:
            return {"ok": True, "place": get_venue().mark(str(body.get("kind")), start, body.get("name"))}
        except ValueError as ex:
            raise HTTPException(422, str(ex)) from ex

    @router.delete("/api/venue/mark/{rid}")
    async def venue_unmark(rid: str):
        if not get_venue().unmark(rid):
            raise HTTPException(404, "No marked place with that id")
        return {"ok": True}

    @router.get("/api/venue/route")
    async def venue_route(to: str, level: str | None = None, u: float | None = None, v: float | None = None,
                          from_room: str | None = None, prefer: str | None = None):
        if prefer not in (None, "stairs", "elevator"):
            raise HTTPException(422, "prefer must be stairs or elevator")
        start = start_from(level, u, v, from_room) or start_from(None, None, None, "117")
        return get_venue().route(to, start, prefer)

    return router


def mount_venue_images(app) -> None:
    app.mount("/venue", StaticFiles(directory=DIR, check_dir=False), name="venue")
