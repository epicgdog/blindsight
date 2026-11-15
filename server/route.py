"""Walking route as a polyline: maneuvers (turns), progress along it and off-route detection.

EntranceGuide and describe_route both use Route, so the spoken guidance and the agent's numbers
agree. Distances are metres along the route. A trusted metric fix sets progress; between fixes
(they are rare) progress advances by the phone's step count times a stride length.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

TURN_DEG = 30.0  # a direction change above this is a maneuver; also the heading tolerance
AROUND_DEG = 150.0  # a turn this sharp is spoken as "turn around"
MIN_LEG_M = 1.0  # a jog shorter than this does not start a maneuver
STRIDE_M = 0.7  # default metres per step
STRIDE_RANGE = (0.4, 1.0)
CAL_SIGMA_M = 1.0  # only fixes this sure calibrate the stride
CAL_MIN_M = 3.0  # two calibration fixes at least this far apart along the route...
CAL_MIN_STEPS = 4  # ...with at least this many steps between them
BACK_M = 2.0  # progress moves back only when a fix is this far behind...
BACK_N = 3  # ...for this many fixes in a row (a single bad fix must not undo progress)
OFF_ROUTE_M = 2.0  # cross-track distance that counts as off the route
OFF_ROUTE_S = 3.0  # ...held for this long


def bearing_deg(a, b) -> float:
    """Direction from a to b in the mesh frame: clockwise from +y, in [0, 360)."""
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360


def turn_between(frm: float, to: float) -> float:
    """Signed turn from heading frm to heading to, in (-180, 180]; positive is right."""
    return (to - frm + 180) % 360 - 180


def clock(turn_deg: float) -> int:
    """Clock position relative to the person's facing: 12 is straight ahead, 3 is right."""
    return int(math.floor((turn_deg + 15) / 30)) % 12 or 12


@dataclass(frozen=True)
class Maneuver:
    s: float  # metres along the route where it happens
    turn_deg: float  # signed turn there (positive is right); 0 for the arrival
    say: str  # "turn left", "turn right", "turn around" or "arrive"
    seg: int  # index of the leg that follows the maneuver


def _turn_word(turn: float) -> str:
    if abs(turn) >= AROUND_DEG:
        return "turn around"
    return "turn right" if turn > 0 else "turn left"


class Route:
    """Polyline from the start point through the scan-point nodes to the entrance."""

    def __init__(self, coords):
        pts = []
        for p in coords:
            p = (float(p[0]), float(p[1]))
            if not pts or math.dist(p, pts[-1]) > 0.05:  # drop repeated points
                pts.append(p)
        self.pts = pts
        self.cum = [0.0]  # metres from the start to each point
        for a, b in zip(pts, pts[1:]):
            self.cum.append(self.cum[-1] + math.dist(a, b))
        self.length = self.cum[-1]
        self.bearings = [bearing_deg(a, b) for a, b in zip(pts, pts[1:])]
        self.turns: list[Maneuver] = []
        for i in range(1, len(pts) - 1):
            # A short jog (e.g. from a start point just off a node) would give a bogus turn.
            if self.cum[i] - self.cum[i - 1] < MIN_LEG_M:
                continue
            turn = turn_between(self.bearings[i - 1], self.bearings[i])
            if abs(turn) > TURN_DEG:
                self.turns.append(Maneuver(self.cum[i], turn, _turn_word(turn), i))
        self.maneuvers = self.turns + [Maneuver(self.length, 0.0, "arrive", len(self.bearings))]

    def segment_at(self, s: float) -> int:
        """Index of the leg that covers metre s along the route."""
        i = bisect.bisect_right(self.cum, s) - 1
        return min(max(i, 0), len(self.bearings) - 1)

    def project(self, xy) -> tuple[float, float, int]:
        """(s along the route, cross-track distance, leg index) of the point nearest to xy."""
        if not self.bearings:
            return 0.0, math.dist(xy, self.pts[0]), 0
        best = None
        for i, (a, b) in enumerate(zip(self.pts, self.pts[1:])):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = self.cum[i + 1] - self.cum[i]  # > 0: repeated points were dropped
            u = min(1.0, max(0.0, ((xy[0] - a[0]) * dx + (xy[1] - a[1]) * dy) / (L * L)))
            d = math.dist(xy, (a[0] + u * dx, a[1] + u * dy))
            if best is None or d < best[0]:
                best = (d, self.cum[i] + u * L, i)
        d, s, i = best
        return s, d, i


class Progress:
    """How far along the route the person is, in metres. Moves forward freely; moves back only on
    a clear, repeated backtrack."""

    def __init__(self, route: Route):
        self.route = route
        self.s = 0.0
        self.stride = STRIDE_M
        self.steps = None  # last cumulative step count seen
        self.anchor = None  # (s, steps) of the last calibration fix, for the stride
        self.behind = 0
        self._off_since = None
        self._off = False

    def update(self, t: float, xy, sigma_m: float | None, steps: int | None, fix: bool = True) -> None:
        """xy: a trusted position, or None when there is none. steps: cumulative step count.
        fix=False: no new position since the last call (an IMU sample between frames), so the stale xy
        is not applied again; only the step count moves."""
        if steps is not None:
            if self.steps is not None and steps > self.steps:
                self.s = min(self.route.length, self.s + (steps - self.steps) * self.stride)
            self.steps = steps
        if not fix:
            return
        if xy is None:
            self.behind, self._off_since, self._off = 0, None, False
            return
        s_fix, cross, _ = self.route.project(xy)
        if s_fix >= self.s:
            self.s, self.behind = s_fix, 0
        elif self.s - s_fix > BACK_M:
            self.behind += 1
            if self.behind >= BACK_N:
                self.s, self.behind = s_fix, 0
        else:
            self.behind = 0
        if sigma_m is not None and sigma_m <= CAL_SIGMA_M and steps is not None:
            self._calibrate(s_fix, steps)
        if cross > OFF_ROUTE_M:
            if self._off_since is None:
                self._off_since = t
            self._off = t - self._off_since >= OFF_ROUTE_S
        else:
            self._off_since, self._off = None, False

    def _calibrate(self, s: float, steps: int) -> None:
        if self.anchor is None or s < self.anchor[0] or steps < self.anchor[1]:
            self.anchor = (s, steps)
            return
        ds, dn = s - self.anchor[0], steps - self.anchor[1]
        if ds >= CAL_MIN_M and dn >= CAL_MIN_STEPS:
            self.stride = min(STRIDE_RANGE[1], max(STRIDE_RANGE[0], ds / dn))
            self.anchor = (s, steps)

    def off_route(self) -> bool:
        return self._off
