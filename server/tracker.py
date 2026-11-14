"""Kalman tracker for the user's position on the floor plan (mesh frame, metres, z up).

State: x, y and their velocities (constant-velocity model); height is kept separately as the
last accepted measurement's z (it only changes with floors). Each verified match gives a
measurement: the PnP camera centre when available, else the matched scan point itself with
a wide sigma.

* Motion: the phone's walking flag sets the process noise. Still: velocity is zeroed and the
  position barely drifts, so repeated fixes average. Walking: accelerations of about 1 m/s^2.
* Gating: a measurement whose Mahalanobis distance exceeds the 99% chi-square bound for 2
  DoF, or whose height is on another floor, is rejected. This is what stops jumps to
  far-away scan points.
* Recovery: if REINIT_N rejected measurements in a row agree with each other within
  REINIT_SPREAD_M, the track is restarted there (the first lock was wrong, or the user really
  moved while nothing matched). Only counted while walking or when the flag is unknown:
  standing still, a look-alike keeps matching the same way and must not win.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

GATE_CHI2 = 9.21  # chi-square, 2 DoF, 99%
FLOOR_DZ_M = 1.2  # a measurement this much higher or lower is on another floor
REINIT_N = 3
REINIT_SPREAD_M = 1.0
ACCEL = {"walking": 1.0, "unknown": 0.7, "still": 0.02}  # process noise, m/s^2
MAX_SPEED = 2.5  # m/s
MAX_DT = 2.0  # predict at most this far in one step (s)


@dataclass
class Measurement:
    xyz: np.ndarray
    sigma_m: float
    place: str | None = None


@dataclass
class TrackStep:
    accepted: bool = False  # measurement fused
    rejected: bool = False  # measurement gated out
    reinit: bool = False  # track restarted at this measurement
    d2: float | None = None  # Mahalanobis distance squared of the innovation


@dataclass
class PositionTracker:
    x: np.ndarray | None = None  # [x, y, vx, vy]
    P: np.ndarray | None = None
    z: float | None = None
    t: float | None = None
    place: str | None = None
    n_accepted: int = 0
    _rejects: list = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.x is not None

    @property
    def xyz(self) -> np.ndarray | None:
        return None if self.x is None else np.array([self.x[0], self.x[1], self.z])

    @property
    def sigma_m(self) -> float | None:
        return None if self.P is None else float(np.sqrt(max(self.P[0, 0], self.P[1, 1])))

    def reset(self) -> None:
        self.x = self.P = self.z = self.t = self.place = None
        self.n_accepted = 0
        self._rejects = []

    def _start(self, m: Measurement, t: float) -> None:
        r2 = m.sigma_m ** 2
        self.x = np.array([m.xyz[0], m.xyz[1], 0.0, 0.0])
        self.P = np.diag([r2, r2, 0.5, 0.5])
        self.z, self.t, self.place = float(m.xyz[2]), t, m.place
        self.n_accepted = 1
        self._rejects = []

    def predict(self, t: float, motion: str) -> None:
        if self.x is None:
            return
        dt = 0.0 if self.t is None else float(np.clip(t - self.t, 0.0, MAX_DT))
        self.t = t
        if motion == "still":
            self.x[2:] = 0.0
            self.P[2:, :] = 0.0
            self.P[:, 2:] = 0.0
            self.P[2, 2] = self.P[3, 3] = 1e-4
        F = np.eye(4)
        F[0, 2] = F[1, 3] = dt
        a2 = ACCEL[motion] ** 2
        q = np.array([[dt ** 4 / 4, dt ** 3 / 2], [dt ** 3 / 2, dt ** 2]]) * a2
        Q = np.zeros((4, 4))
        Q[np.ix_([0, 2], [0, 2])] = q
        Q[np.ix_([1, 3], [1, 3])] = q
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q
        sp = float(np.hypot(self.x[2], self.x[3]))
        if sp > MAX_SPEED:
            self.x[2:] *= MAX_SPEED / sp

    def step(self, t: float, moving: bool | None, m: Measurement | None) -> TrackStep:
        motion = "unknown" if moving is None else ("walking" if moving else "still")
        if self.x is None:
            if m is None:
                return TrackStep()
            self._start(m, t)
            return TrackStep(accepted=True, d2=0.0)
        self.predict(t, motion)
        if m is None:
            return TrackStep()
        H = np.zeros((2, 4))
        H[0, 0] = H[1, 1] = 1.0
        R = np.eye(2) * m.sigma_m ** 2
        y = np.asarray(m.xyz[:2], float) - self.x[:2]
        S = H @ self.P @ H.T + R
        d2 = float(y @ np.linalg.solve(S, y))
        other_floor = abs(float(m.xyz[2]) - self.z) > FLOOR_DZ_M
        if d2 > GATE_CHI2 or other_floor:
            if motion != "still":
                self._rejects.append(m)
                self._rejects = self._rejects[-REINIT_N:]
                if len(self._rejects) >= REINIT_N:
                    pts = np.array([r.xyz for r in self._rejects])
                    if float(np.max(np.linalg.norm(pts - pts.mean(0), axis=1))) <= REINIT_SPREAD_M:
                        self._start(m, t)
                        return TrackStep(accepted=True, reinit=True, d2=d2)
            return TrackStep(rejected=True, d2=d2)
        self._rejects = []
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ y
        self.P = (np.eye(4) - K @ H) @ self.P
        self.z = float(m.xyz[2])
        self.place = m.place or self.place
        self.n_accepted += 1
        return TrackStep(accepted=True, d2=d2)


def fix_sigma(inliers: int, offset_m: float) -> float:
    """Measurement sigma (m) for a PnP fix: fewer inliers and larger offsets are less certain."""
    s = 0.15 * np.sqrt(60.0 / max(12, int(inliers))) * (1.0 + 0.3 * float(offset_m))
    return float(np.clip(s, 0.05, 1.5))


SCAN_POINT_SIGMA_M = 1.0  # a verified match without PnP: the user is near that scan point
