"""Metric camera position from a verified match (Case A: depth from the scan mesh, then PnP).

A verified match pairs live-frame pixels with pixels of a reference view rendered from a pano.
Each reference pixel is a ray from the pano centre; casting it into the scan mesh gives a 3D
point. With 3D points and their live pixels, PnP solves the live camera's full pose, so the
user no longer has to stand on the scan point.

Frames
------
* pano-local: origin at the pano centre, the frame of geometry.cam_to_world (y up, yaw 0
  looking along +z). Reference rays and PnP live here.
* mesh: the scan's frame (Matterport: z up, metres, same coordinates as panoLocations).
  A place maps pano-local to mesh with ``p_mesh = origin + M @ p_local``.

M is not documented by Matterport. ``frame_candidates`` lists the plausible ones and
``tools/calibrate_pano_frame.py`` picks the one that makes cross-pano PnP land on the
other pano's surveyed position.
"""

from __future__ import annotations

import json
import logging
import math
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from geometry import bearings, cam_to_world

log = logging.getLogger("blindsight.position")

PNP_REPROJ_PX = 4.0  # RANSAC reprojection threshold, pixels at working size
PNP_MIN_INLIERS = 12
PNP_MAX_OFFSET_M = 6.0  # a solve farther than this from the pano is not trusted
FLIP_Y = np.array([1.0, -1.0, 1.0])


# ---------------------------------------------------------------- depth sources
class BoxDepth:
    """Axis-aligned box room (tests): rays from inside hit the walls, floor or ceiling."""

    def __init__(self, lo, hi):
        self.lo, self.hi = np.asarray(lo, float), np.asarray(hi, float)

    def cast(self, origin: np.ndarray, dirs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        d = np.asarray(dirs, float).reshape(-1, 3)
        o = np.asarray(origin, float)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(d > 0, (self.hi - o) / d, np.where(d < 0, (self.lo - o) / d, np.inf))
        tmin = t.min(axis=1)
        ok = np.isfinite(tmin) & (tmin > 0)
        return o + np.where(ok, tmin, 0.0)[:, None] * d, ok


def ray_caster(mesh):
    """Ray intersector for a trimesh mesh: Embree (embreex, native code) when importable, else trimesh's
    pure-Python triangle caster (about 150 rays/s on the mesh). The choice is logged once per load."""
    import trimesh
    from trimesh.ray import ray_triangle

    if trimesh.ray.has_embree:
        from trimesh.ray.ray_pyembree import RayMeshIntersector

        name = "embree"
    else:
        RayMeshIntersector, name = ray_triangle.RayMeshIntersector, "triangle"
    log.info("ray casting with %s", name)
    return RayMeshIntersector(mesh)


class MeshDepth:
    """Triangle mesh (trimesh), first hit per ray. Works in the mesh frame."""

    def __init__(self, mesh):
        self.mesh = mesh
        self.ray = ray_caster(mesh)

    @classmethod
    def load(cls, path: str | Path) -> "MeshDepth":
        import trimesh

        return cls(trimesh.load(str(path), force="mesh", process=False))

    def cast(self, origin: np.ndarray, dirs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        d = np.asarray(dirs, float).reshape(-1, 3)
        pts = np.zeros_like(d)
        ok = np.zeros(len(d), bool)
        if len(d) == 0:
            return pts, ok
        o = np.broadcast_to(np.asarray(origin, float), d.shape)
        loc, idx, _ = self.ray.intersects_location(o, d, multiple_hits=False)
        pts[idx], ok[idx] = loc, True
        return pts, ok


@dataclass
class PlaceFrame:
    """Where a pano sits in the depth source's frame: p_depth = origin + M @ p_local."""

    origin: np.ndarray
    M: np.ndarray = field(default_factory=lambda: np.eye(3))

    def to_depth(self, p_local: np.ndarray) -> np.ndarray:
        return self.origin + np.asarray(p_local, float) @ self.M.T

    def to_local(self, p_depth: np.ndarray) -> np.ndarray:
        return (np.asarray(p_depth, float) - self.origin) @ self.M


def cast_local(depth, frame: PlaceFrame, dirs_local: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cast pano-local rays from the pano centre; hits come back in pano-local coordinates."""
    d = np.asarray(dirs_local, float).reshape(-1, 3) @ frame.M.T
    hits, ok = depth.cast(frame.origin, d)
    return frame.to_local(hits), ok


# ---------------------------------------------------------------- frame candidates
def quat_to_matrix(q: dict | tuple) -> np.ndarray:
    if isinstance(q, dict):
        x, y, z, w = (float(q[k]) for k in "xyzw")
    else:
        x, y, z, w = (float(v) for v in q)
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def rot_z(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


# pano-local (x right, y up, z forward at yaw 0) -> z-up frame, pano y -> mesh z.
SWAP = {
    "a": np.array([[1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]]),  # x->x, z->y (det -1)
    "b": np.array([[-1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]]),  # x->-x, z->y (det +1)
}


def frame_candidates() -> list[dict]:
    """Every (swap, base yaw offset, quaternion use) combination, as parameter dicts."""
    return [{"swap": s, "base_deg": b, "quat": q}
            for s in SWAP for b in (0, 90, 180, 270) for q in ("q", "q_inv", "none")]


def frame_matrix(cand: dict, quat) -> np.ndarray:
    Rq = quat_to_matrix(quat) if quat is not None else np.eye(3)
    Rq = {"q": Rq, "q_inv": Rq.T, "none": np.eye(3)}[cand["quat"]]
    return Rq @ rot_z(cand["base_deg"]) @ SWAP[cand["swap"]]


# ---------------------------------------------------------------- PnP
@dataclass
class PositionFix:
    local: np.ndarray  # camera centre, pano-local metres
    R_cw: np.ndarray  # camera-to-pano-local rotation (columns: right, down, forward)
    inliers: int
    n_points: int
    reproj_px: float
    world: np.ndarray | None = None  # camera centre in the depth source's frame

    @property
    def offset_m(self) -> float:
        return float(np.linalg.norm(self.local))

    def to_dict(self) -> dict:
        d = {"local": [round(float(v), 3) for v in self.local], "inliers": self.inliers,
             "n_points": self.n_points, "reproj_px": round(self.reproj_px, 2), "offset_m": round(self.offset_m, 3)}
        if self.world is not None:
            d["world"] = [round(float(v), 3) for v in self.world]
        return d


def solve_pnp(world_pts: np.ndarray, live_pts: np.ndarray, K_live: np.ndarray,
              R_cw_guess: np.ndarray | None = None, reproj_px: float = PNP_REPROJ_PX) -> PositionFix | None:
    """Camera pose from 3D points (pano-local) and their live pixels.

    geometry.cam_to_world has columns (right, down, forward) in a y-up frame, so its determinant
    is -1: the pano-local frame is left-handed relative to OpenCV's camera. PnP needs a proper
    rotation, so it runs on y-flipped points (y down) and the answer is flipped back.
    """
    X = np.ascontiguousarray(np.asarray(world_pts, np.float64).reshape(-1, 3) * FLIP_Y, np.float64)
    x = np.ascontiguousarray(live_pts, np.float64).reshape(-1, 2)
    if len(X) < 6:
        return None
    kw = dict(iterationsCount=300, reprojectionError=reproj_px, confidence=0.999)
    if R_cw_guess is not None:
        rvec0, _ = cv2.Rodrigues(np.ascontiguousarray((np.diag(FLIP_Y) @ R_cw_guess).T))
        ok, rvec, tvec, inl = cv2.solvePnPRansac(X, x, K_live, None, rvec0.copy(), np.zeros((3, 1)),
                                                 useExtrinsicGuess=True, flags=cv2.SOLVEPNP_ITERATIVE, **kw)
    else:
        ok, rvec, tvec, inl = cv2.solvePnPRansac(X, x, K_live, None, flags=cv2.SOLVEPNP_SQPNP, **kw)
    if not ok or inl is None or len(inl) < 6:
        return None
    inl = inl.ravel()
    rvec, tvec = cv2.solvePnPRefineLM(X[inl], x[inl], K_live, None, rvec, tvec)
    R_wc, _ = cv2.Rodrigues(rvec)
    proj, _ = cv2.projectPoints(X[inl], rvec, tvec, K_live, None)
    err = float(np.median(np.linalg.norm(proj.reshape(-1, 2) - x[inl], axis=1)))
    centre = (-R_wc.T @ tvec).ravel() * FLIP_Y
    return PositionFix(centre, np.diag(FLIP_Y) @ R_wc.T, int(len(inl)), int(len(X)), err)


def position_from_match(v, ref_yaw: float, ref_pitch: float, ref_roll: float, K_ref: np.ndarray,
                        K_live: np.ndarray, depth, frame: PlaceFrame,
                        min_inliers: int = PNP_MIN_INLIERS,
                        max_offset_m: float = PNP_MAX_OFFSET_M) -> PositionFix | None:
    """PositionFix for a geometry.Verification against a reference view, or None."""
    live = v.match_live_pts if v.match_live_pts is not None else v.live_pts
    ref = v.match_ref_pts if v.match_ref_pts is not None else v.ref_pts
    if live is None or ref is None or len(ref) < min_inliers:
        return None
    C_ref = cam_to_world(ref_yaw, ref_pitch, ref_roll)
    dirs = bearings(ref, K_ref) @ C_ref.T
    pts, ok = cast_local(depth, frame, dirs)
    if ok.sum() < min_inliers:
        return None
    guess = C_ref @ v.R.T if v.R is not None else None
    fix = solve_pnp(pts[ok], live[ok], K_live, guess)
    if fix is None or fix.inliers < min_inliers or fix.offset_m > max_offset_m:
        return None
    fix.world = frame.to_depth(fix.local)
    return fix


# ---------------------------------------------------------------- runtime solver
PANO_FRAME_FILE = Path("matterport") / "pano_frame.json"  # written by tools/calibrate_pano_frame.py
PANO_ROT_FILE = Path("matterport") / "pano_rotations.json"


class PositionSolver:
    """Per-frame metric position for the localizer.

    Reference keypoints never move, so the 3D point of each (reference view, keypoint) is ray
    cast once and cached. Only keypoints that actually got matched are cast. With
    ``async_cast`` the casting runs on a worker thread: a frame never waits for the mesh, it
    solves from the points already cached and queues the rest, so a position appears a frame
    or two after the user first faces a new reference view (trimesh's pure-Python caster
    does about 150 rays/s).
    """

    def __init__(self, depth, frames: dict[str, PlaceFrame], max_cache: int = 4000, async_cast: bool = False):
        self.depth = depth
        self.frames = frames
        self._cache: dict = {}
        self.last: dict = {}
        self._max_cache = max_cache
        self._lock = threading.Lock()
        self._queue: queue.Queue | None = None
        if async_cast:
            self._queue = queue.Queue()
            threading.Thread(target=self._worker, name="position-raycast", daemon=True).start()

    @classmethod
    def load(cls, data_dir: str | Path, async_cast: bool = True) -> "PositionSolver | None":
        """From data_dir/matterport/pano_frame.json (calibrated frame + mesh path); None if absent."""
        data_dir = Path(data_dir)
        cfg_path, rot_path = data_dir / PANO_FRAME_FILE, data_dir / PANO_ROT_FILE
        if not cfg_path.is_file() or not rot_path.is_file():
            return None
        cfg = json.loads(cfg_path.read_text())
        mesh = Path(cfg["mesh"])
        if not mesh.is_absolute():
            mesh = data_dir / mesh if (data_dir / mesh).exists() else data_dir.parent / mesh
        if not mesh.is_file():
            return None
        frames = {}
        for pid, d in json.loads(rot_path.read_text()).items():
            p = d["position"]
            frames[pid] = PlaceFrame(np.array([p["x"], p["y"], p["z"]], float),
                                     frame_matrix(cfg["candidate"], d.get("rotation")))
        return cls(MeshDepth.load(mesh), frames, async_cast=async_cast)

    # -- cache: per reference view, a 3D point and a state per keypoint -----------
    UNKNOWN, HIT, MISS, PENDING = 0, 1, 2, 3

    def _entry(self, key, n: int) -> dict:
        e = self._cache.get(key)
        if e is None:
            if len(self._cache) >= self._max_cache:
                self._cache.pop(next(iter(self._cache)))
            e = {"pts": np.zeros((n, 3)), "state": np.zeros(n, np.int8)}
            self._cache[key] = e
        return e

    def _cast(self, e: dict, idx: np.ndarray, dirs: np.ndarray, frame: PlaceFrame) -> None:
        pts, ok = cast_local(self.depth, frame, dirs)
        with self._lock:
            e["pts"][idx] = pts
            e["state"][idx] = np.where(ok, self.HIT, self.MISS)

    def _worker(self) -> None:
        while True:
            job = self._queue.get()
            try:
                self._cast(*job)
            except Exception:
                with self._lock:  # let a later frame retry
                    e, idx = job[0], job[1]
                    e["state"][idx] = self.UNKNOWN
            finally:
                self._queue.task_done()

    def wait(self) -> None:
        """Block until queued ray casts are done (tests and offline evaluation)."""
        if self._queue is not None:
            self._queue.join()

    def ref_points(self, key, place: str, kpts: np.ndarray, yaw: float, pitch: float, roll: float,
                   K_ref: np.ndarray, idx: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray] | None:
        """(pts, hit) for keypoints idx (default: all) of one reference view, pano-local.
        Keypoints not cast yet are cast now, or queued when async (hit is False until done)."""
        frame = self.frames.get(place)
        if frame is None:
            return None
        kpts = np.asarray(kpts, float)
        idx = np.arange(len(kpts)) if idx is None else np.unique(np.asarray(idx, int))
        with self._lock:
            e = self._entry(key, len(kpts))
            todo = idx[e["state"][idx] == self.UNKNOWN]
            if len(todo) and self._queue is not None:
                e["state"][todo] = self.PENDING
        if len(todo):
            dirs = bearings(kpts[todo], K_ref) @ cam_to_world(yaw, pitch, roll).T
            if self._queue is not None:
                self._queue.put((e, todo, dirs, frame))
            else:
                self._cast(e, todo, dirs, frame)
        with self._lock:
            return e["pts"].copy(), e["state"] == self.HIT

    def solve(self, v, key, place: str, kpts: np.ndarray, yaw: float, pitch: float, roll: float,
              K_ref: np.ndarray, K_live: np.ndarray) -> PositionFix | None:
        # self.last: what this solve saw and where it stopped, for the debug server.
        last = self.last = {"place": place, "ref": key[1] if isinstance(key, tuple) else str(key),
                            "pairs": 0, "mesh_hits": 0, "pending": 0, "result": None}
        if v.match_ref_idx is None or v.match_live_pts is None or len(v.match_ref_idx) < PNP_MIN_INLIERS:
            last["result"] = f"too few 2D-2D matches (< {PNP_MIN_INLIERS})"
            return None
        idx = np.asarray(v.match_ref_idx, int)
        last["pairs"] = int(len(idx))
        rp = self.ref_points(key, place, kpts, yaw, pitch, roll, K_ref, idx)
        if rp is None:
            last["result"] = "no mesh frame for this pano"
            return None
        pts, ok = rp
        sel = ok[idx]
        last["mesh_hits"] = int(sel.sum())
        with self._lock:
            e = self._cache.get(key)
            last["pending"] = int((e["state"][idx] == self.PENDING).sum()) if e is not None else 0
        if sel.sum() < PNP_MIN_INLIERS:
            last["result"] = f"too few rays hit the mesh yet (< {PNP_MIN_INLIERS}; {last['pending']} still casting)"
            return None
        guess = cam_to_world(yaw, pitch, roll) @ v.R.T if v.R is not None else None
        fix = solve_pnp(pts[idx[sel]], v.match_live_pts[sel], K_live, guess)
        if fix is None:
            last["result"] = "PnP RANSAC failed"
            return None
        last.update(pnp_inliers=fix.inliers, reproj_px=round(fix.reproj_px, 2), offset_m=round(fix.offset_m, 2),
                    world=[round(float(x), 3) for x in self.frames[place].to_depth(fix.local)])
        if fix.inliers < PNP_MIN_INLIERS:
            last["result"] = f"too few PnP inliers (< {PNP_MIN_INLIERS})"
            return None
        if fix.offset_m > PNP_MAX_OFFSET_M:
            last["result"] = f"pose {fix.offset_m:.1f} m from the pano (> {PNP_MAX_OFFSET_M})"
            return None
        fix.world = self.frames[place].to_depth(fix.local)
        last["result"] = "ok"
        return fix
