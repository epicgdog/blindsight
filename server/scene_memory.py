"""What the camera sees now and saw recently, as a short summary for the voice agent."""

from __future__ import annotations

from dataclasses import dataclass

from server.cues import SPOKEN_NAMES

MEMORY_S = 60.0


@dataclass
class Seen:
    cls: str
    place: str | None
    first_t: float
    last_t: float
    direction: str
    distance: str | None
    times: int = 1


class SceneMemory:
    def __init__(self, memory_s: float = MEMORY_S):
        self.memory_s = memory_s
        self.now_objects: list[dict] = []
        self.seen: dict[tuple[str, str | None], Seen] = {}

    def update(self, t: float, described: list[dict], place: str | None) -> None:
        """described: server.cues.describe() dicts for this frame."""
        self.now_objects = [d for d in described if d.get("distance") is not None]
        for d in self.now_objects:
            key = (d["cls"], place)
            s = self.seen.get(key)
            if s is None:
                self.seen[key] = Seen(d["cls"], place, t, t, d["direction"], d["distance"])
            else:
                s.last_t, s.direction, s.distance, s.times = t, d["direction"], d["distance"], s.times + 1
        self.seen = {k: s for k, s in self.seen.items() if t - s.last_t <= self.memory_s}

    def summary(self, t: float, place_label=lambda p: p) -> dict:
        def name(cls):
            return SPOKEN_NAMES.get(cls, cls)

        now = [", ".join(v for v in (name(d["cls"]), d["direction"], d["distance"]) if v)
               for d in self.now_objects]
        recent = [{"object": name(s.cls), "where": place_label(s.place) if s.place else "unknown place",
                   "seconds_ago": round(t - s.last_t), "last_direction": s.direction, "times_seen": s.times}
                  for s in sorted(self.seen.values(), key=lambda s: -s.last_t)]
        return {"now": now, "recent": recent[:15]}

    def reset(self) -> None:
        self.now_objects, self.seen = [], {}
