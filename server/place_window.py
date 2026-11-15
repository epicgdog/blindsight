"""Which scan point the user is at, decided over a sliding window of recent frames.

A frame "wins" for its top place when the verified geometry is strong and clear:

    i1 >= N_MIN  and  i1 >= MARGIN * i2

i1 / i2 are the inliers of the best / second-best place. Over the last WINDOW_S seconds, the
place with the most wins is the decision once it has MIN_WINS of them (consistency over frames,
not one perfect frame). Its conf is its share of the window's frames. With no deciding place the
previous decision is held; after UNKNOWN_AFTER_S without a win it becomes unknown.

The earlier rule, one frame with (i1/(i1+N_MIN)) * (i1/(i1+i2)) > 0.70, never fired once the
rival place was verified too: a steady 125 vs 80 inliers scores 0.48 on every frame. It is
kept as frame_confidence() for display.
"""

from __future__ import annotations

import os
from collections import deque
from dataclasses import asdict, dataclass

# Tuned for ~1 frame/s on the phone: neighbouring scan points (2-3 m apart) often score within
# 10-20 % of each other, and 3 wins in 5 s at 1 fps with a 1.3x margin almost never happened.
WINDOW_S = float(os.environ.get("PLACE_WINDOW_S", 8.0))
MARGIN = float(os.environ.get("PLACE_MARGIN", 1.1))  # a win needs this many times the runner-up's inliers
MIN_WINS = int(os.environ.get("PLACE_MIN_WINS", 2))  # wins within the window that decide a place
UNKNOWN_AFTER_S = 10.0
N_MIN = 30


def _ranked(ranking: list[dict] | None) -> list[dict]:
    return sorted((r for r in ranking or () if int(r.get("inliers") or 0) > 0),
                  key=lambda r: -int(r["inliers"]))


def frame_confidence(ranking: list[dict] | None, n_min: int = N_MIN) -> tuple[str | None, float]:
    """(top place, strength x ambiguity in [0, 1)), for display only."""
    rows = _ranked(ranking)
    if not rows:
        return None, 0.0
    i1 = float(rows[0]["inliers"])
    i2 = float(rows[1]["inliers"]) if len(rows) > 1 else 0.0
    return rows[0]["place"], (i1 / (i1 + n_min)) * (i1 / (i1 + i2))


def frame_winner(ranking: list[dict] | None, n_min: int = N_MIN, margin: float = MARGIN) -> str | None:
    """The place this frame clearly supports, or None."""
    rows = _ranked(ranking)
    if not rows:
        return None
    i1 = int(rows[0]["inliers"])
    i2 = int(rows[1]["inliers"]) if len(rows) > 1 else 0
    return rows[0]["place"] if i1 >= n_min and i1 >= margin * i2 else None


@dataclass
class Decision:
    place: str | None
    conf: float
    state: str  # "confident" (enough wins in the window), "holding" or "unknown"
    confident_frames: int
    since_confident_s: float | None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["conf"] = round(self.conf, 3)
        if self.since_confident_s is not None:
            d["since_confident_s"] = round(self.since_confident_s, 1)
        return d


class PlaceWindow:
    def __init__(self, window_s: float = WINDOW_S, min_wins: int = MIN_WINS, margin: float = MARGIN,
                 unknown_after_s: float = UNKNOWN_AFTER_S, n_min: int = N_MIN):
        self.window_s, self.min_wins, self.margin = window_s, min_wins, margin
        self.unknown_after_s, self.n_min = unknown_after_s, n_min
        self.frames: deque[tuple[float, str | None]] = deque()  # (t, winning place or None)
        self.place: str | None = None
        self.conf = 0.0
        self.last_confident_t: float | None = None

    def push(self, t: float, ranking: list[dict] | None) -> Decision:
        self.frames.append((t, frame_winner(ranking, self.n_min, self.margin)))
        return self.decide(t)

    def decide(self, t: float) -> Decision:
        while self.frames and self.frames[0][0] < t - self.window_s:
            self.frames.popleft()
        counts: dict[str, int] = {}
        for _, p in self.frames:
            if p is not None:
                counts[p] = counts.get(p, 0) + 1
        if counts:
            place, wins = max(counts.items(), key=lambda kv: (kv[1], kv[0] == self.place))
            if wins >= self.min_wins:
                self.place, self.conf = place, wins / max(1, len(self.frames))
                self.last_confident_t = max(ts for ts, p in self.frames if p == place)
                return Decision(place, self.conf, "confident", wins, t - self.last_confident_t)
        since = None if self.last_confident_t is None else t - self.last_confident_t
        if self.place is not None and since is not None and since <= self.unknown_after_s:
            return Decision(self.place, self.conf, "holding", 0, since)
        self.place, self.conf = None, 0.0
        return Decision(None, 0.0, "unknown", 0, since)

    def reset(self) -> None:
        self.frames.clear()
        self.place, self.conf, self.last_confident_t = None, 0.0, None
