"""Which scan point the user is at, decided over a sliding window of recent frames.

Each frame gets one confidence for its top place from the verified geometry:

    conf = (i1 / (i1 + N_MIN)) * (i1 / (i1 + i2))

i1 / i2 are the inliers of the best / second-best place. The first factor is strength (70
inliers alone gives 0.70), the second ambiguity (two places tied at 100 inliers give 0.38).
Over the last WINDOW_S seconds, frames with conf > CONF_MIN are confident; the place of the
most confident one is the decision (ties: more confident frames). No confident frame holds the
previous decision; none for UNKNOWN_AFTER_S makes it unknown.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass

WINDOW_S = 5.0
CONF_MIN = 0.70
UNKNOWN_AFTER_S = 10.0
N_MIN = 30


def frame_confidence(ranking: list[dict] | None, n_min: int = N_MIN) -> tuple[str | None, float]:
    """(top place, confidence in [0, 1)) from a match ranking of {place, inliers, ...}."""
    rows = sorted((r for r in ranking or () if int(r.get("inliers") or 0) > 0),
                  key=lambda r: -int(r["inliers"]))
    if not rows:
        return None, 0.0
    i1 = float(rows[0]["inliers"])
    i2 = float(rows[1]["inliers"]) if len(rows) > 1 else 0.0
    return rows[0]["place"], (i1 / (i1 + n_min)) * (i1 / (i1 + i2))


@dataclass
class Decision:
    place: str | None
    conf: float
    state: str  # "confident" (a confident frame in the window), "holding" or "unknown"
    confident_frames: int
    since_confident_s: float | None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["conf"] = round(self.conf, 3)
        if self.since_confident_s is not None:
            d["since_confident_s"] = round(self.since_confident_s, 1)
        return d


class PlaceWindow:
    def __init__(self, window_s: float = WINDOW_S, conf_min: float = CONF_MIN,
                 unknown_after_s: float = UNKNOWN_AFTER_S, n_min: int = N_MIN):
        self.window_s, self.conf_min, self.unknown_after_s, self.n_min = window_s, conf_min, unknown_after_s, n_min
        self.frames: deque[tuple[float, str | None, float]] = deque()
        self.place: str | None = None
        self.conf = 0.0
        self.last_confident_t: float | None = None

    def push(self, t: float, ranking: list[dict] | None) -> Decision:
        place, conf = frame_confidence(ranking, self.n_min)
        self.frames.append((t, place, conf))
        while self.frames and self.frames[0][0] < t - self.window_s:
            self.frames.popleft()
        return self.decide(t)

    def decide(self, t: float) -> Decision:
        good = [(c, p) for _, p, c in self.frames if p is not None and c > self.conf_min]
        if good:
            counts: dict[str, int] = {}
            for _, p in good:
                counts[p] = counts.get(p, 0) + 1
            conf, place = max(good, key=lambda cp: (cp[0], counts[cp[1]]))
            self.place, self.conf, self.last_confident_t = place, conf, t
            return Decision(place, conf, "confident", counts[place], 0.0)
        since = None if self.last_confident_t is None else t - self.last_confident_t
        if self.place is not None and since is not None and since <= self.unknown_after_s:
            return Decision(self.place, self.conf, "holding", 0, since)
        self.place, self.conf = None, 0.0
        return Decision(None, 0.0, "unknown", 0, since)

    def reset(self) -> None:
        self.frames.clear()
        self.place, self.conf, self.last_confident_t = None, 0.0, None
