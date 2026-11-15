"""Kalman filter for which way the person faces, in the floor map's (mesh) frame.

State: h (heading, degrees clockwise from mesh +y) and b (compass bias: mesh heading minus the
phone's compass heading). Both are angles; every innovation is wrapped to [-180, 180).

* Predict: h is a random walk whose noise follows motion (turning in place or walking makes it
  change fast); b is a slow random walk (the magnetic disturbance indoors changes over metres).
* Compass measurement, every frame: z = h - b, sigma COMPASS_SIGMA. With b learned this tracks turns
  smoothly between visual fixes.
* Visual measurement, when a frame verifies: z = h. From PnP (the camera rotation solved from many
  3D mesh points; used only when it agrees with the matched view's yaw) or else the matched
  reference view's yaw. Visual fixes are what make h absolute and what teach b.
* Gating: a visual fix whose normalized innovation exceeds the 99% chi-square bound (1 DoF) is
  rejected; REINIT_N rejected fixes in a row that agree with each other restart the filter there.
* Course (walking direction) measurement, when the tracker says the person is walking: z = course,
  sigma COURSE_SIGMA. The phone mostly faces the way it walks, so this only steers h between fixes.
* Sensor samples (phone IMU stream, ~10 Hz) run predict + compass only; visual fixes arrive with
  frames, a little late, so their heading is shifted by the compass change since capture.
* Until the first visual fix there is no mesh-frame heading (the compass alone has an unknown
  offset), so the estimate is None.
"""

from __future__ import annotations

import math

import numpy as np

from server.entrance_nav import mesh_heading_deg

PNP_AGREE_DEG = 25.0
SIGMA = {"pnp": 6.0, "view": 12.0, "course": 20.0}
COMPASS_SIGMA = 8.0
Q_TURN_STILL = 30.0  # deg / sqrt(s): heading random walk, phone held still
Q_TURN_MOVING = 90.0  # deg / sqrt(s): walking or turning
Q_BIAS = 1.5  # deg / sqrt(s): compass bias drift
INIT_BIAS_SIGMA = 60.0
GATE_CHI2 = 6.63  # 99%, 1 DoF
REINIT_N = 3
REINIT_SPREAD_DEG = 20.0
MAX_SIGMA_DEG = 45.0  # report no heading beyond this


def wrap180(d: float) -> float:
    return (d + 180.0) % 360.0 - 180.0


class HeadingKalman:
    def __init__(self, frames: dict):
        self.frames = frames  # pano id -> pano-local to mesh rotation (EntranceMap.frames)
        self.reset()

    def reset(self) -> None:
        self.x: np.ndarray | None = None  # [h, b]
        self.P = np.eye(2)
        self.t: float | None = None
        self.has_bias = False
        self.rejected: list[float] = []
        self.last_source: str | None = None
        self.estimate: dict = {"heading_deg": None, "source": None, "sigma_deg": None}

    # -- inputs ------------------------------------------------------------------
    def _visual(self, obs: dict | None) -> tuple[float, str] | None:
        if not obs or obs.get("yaw") is None:
            return None
        M = self.frames.get(obs.get("pano"))
        if M is None:
            return None
        yaw, source = float(obs["yaw"]), "view"
        pnp = obs.get("pnp_yaw")
        if pnp is not None and abs(wrap180(float(pnp) - yaw)) <= PNP_AGREE_DEG:
            yaw, source = float(pnp), "pnp"
        return mesh_heading_deg(M, yaw) % 360.0, source

    def _init(self, h: float, compass: float | None, sigma: float) -> None:
        b = wrap180(h - compass) if compass is not None else 0.0
        self.x = np.array([h, b])
        self.P = np.diag([sigma ** 2, (sigma ** 2 + COMPASS_SIGMA ** 2) if compass is not None else INIT_BIAS_SIGMA ** 2])
        self.has_bias = compass is not None
        self.rejected = []

    def _predict(self, t: float, moving: bool | None) -> None:
        dt = 0.0 if self.t is None else max(0.0, min(5.0, t - self.t))
        q = Q_TURN_STILL if moving is False else Q_TURN_MOVING
        self.P = self.P + np.diag([(q ** 2) * dt, (Q_BIAS ** 2) * dt])

    def _update(self, H: np.ndarray, z: float, r: float, gate: bool) -> bool:
        pred = float(H @ self.x)
        y = wrap180(z - pred)
        S = float(H @ self.P @ H) + r ** 2
        if gate and y * y / S > GATE_CHI2:
            return False
        K = (self.P @ H) / S
        self.x = self.x + K * y
        self.x[0] %= 360.0
        self.x[1] = wrap180(self.x[1])
        self.P = (np.eye(2) - np.outer(K, H)) @ self.P
        return True

    # -- inputs by time: sensor samples and frames ------------------------------
    def _predict_to(self, t: float, moving: bool | None) -> None:
        if self.x is not None:
            self._predict(t, moving)
        self.t = t if self.t is None else max(self.t, t)

    def _fuse_heading(self, h: float, source: str, compass: float | None) -> None:
        """Gated update toward heading h; visual repeats that agree restart the filter there."""
        if source == "course":
            # A course fix is only a sideways nudge: it never restarts the filter or resets its rejects.
            if self._update(np.array([1.0, 0.0]), h, SIGMA[source], gate=True):
                self.last_source = source
            return
        if self._update(np.array([1.0, 0.0]), h, SIGMA[source], gate=True):
            self.rejected = []
            self.last_source = source
            return
        self.rejected.append(h)
        recent = self.rejected[-REINIT_N:]
        if len(recent) >= REINIT_N and max(abs(wrap180(a - recent[-1])) for a in recent) <= REINIT_SPREAD_DEG:
            self._init(h, compass, SIGMA[source])  # the filter was wrong; restart on the fixes
            self.last_source = source

    def update_sensors(self, t: float, compass_deg: float | None, moving: bool | None = None) -> dict:
        """Predict to t and fuse the compass. Runs on every phone IMU sample, and on frames when the
        IMU stream is not there (compass_deg None skips the compass part)."""
        self._predict_to(t, moving)
        compass = None if compass_deg is None else float(compass_deg) % 360.0
        if self.x is not None and compass is not None:
            if not self.has_bias:
                # First compass reading after a compass-less start: learn the bias from h.
                self.x[1] = wrap180(self.x[0] - compass)
                self.P[1, 1] = self.P[0, 0] + COMPASS_SIGMA ** 2
                self.P[0, 1] = self.P[1, 0] = self.P[0, 0]
                self.has_bias = True
            else:
                self._update(np.array([1.0, -1.0]), compass, COMPASS_SIGMA, gate=False)
                self.last_source = "compass"
        return self._refresh()

    def update_visual(self, t: float, obs: dict | None, compass_deg: float | None = None,
                      moving: bool | None = None, shift_deg: float = 0.0) -> dict:
        """Fuse a frame's visual heading. shift_deg moves it from the capture time to now (the compass
        change since capture), so an old frame is compared with the present state."""
        self._predict_to(t, moving)
        vis = self._visual(obs)
        if vis is None:
            return self._refresh()
        compass = None if compass_deg is None else float(compass_deg) % 360.0
        h, source = (vis[0] + shift_deg) % 360.0, vis[1]
        if self.x is None:
            self._init(h, compass, SIGMA[source])
            self.last_source = source
        else:
            self._fuse_heading(h, source, compass)
        return self._refresh()

    def update_course(self, t: float, course_deg: float, moving: bool | None = None) -> dict:
        """Walking direction from the tracker's velocity (mesh frame). Only refines a running filter:
        the course alone does not say which way the phone faces, so it never starts one."""
        self._predict_to(t, moving)
        if self.x is not None:
            self._fuse_heading(float(course_deg) % 360.0, "course", None)
        return self._refresh()

    def update(self, t: float, obs: dict | None, compass_deg: float | None, moving: bool | None = None) -> dict:
        """One frame with no capture-time correction: sensors, then its visual fix."""
        self.update_sensors(t, compass_deg, moving)
        return self.update_visual(t, obs, compass_deg, moving)

    def _refresh(self) -> dict:
        if self.x is None:
            est = {"heading_deg": None, "source": None, "sigma_deg": None}
        else:
            sigma = math.sqrt(max(0.0, self.P[0, 0]))
            est = {"heading_deg": round(float(self.x[0]) % 360.0, 1) if sigma <= MAX_SIGMA_DEG else None,
                   "source": self.last_source,
                   "sigma_deg": round(sigma, 1),
                   "compass_bias_deg": round(float(self.x[1]), 1) if self.has_bias else None,
                   "bias_sigma_deg": round(math.sqrt(max(0.0, self.P[1, 1])), 1) if self.has_bias else None}
        self.estimate = est
        return est


HeadingFuser = HeadingKalman  # old name
