"""Part 3 geometry: camera model, contrast normalization, movable-object masking,
local features, geometric verification with RANSAC, and heading from rotation.

Conventions shared by refs.py, localize.py and the Part 3 tools:

* Pano world frame: x right, y up, z forward. Yaw is in degrees, clockwise seen from
  above (turning right increases yaw), 0 is the centre of horizon face 1 once the face
  layout has been verified (see refs.detect_layout). Pitch is positive looking up.
* Camera frame: OpenCV, x right, y down, z forward. Pixel centres sit on integer
  coordinates, so the principal point is ((w - 1) / 2, (h - 1) / 2). SIFT and
  SuperPoint (origin="center") both report keypoints in this convention.
* A camera pose is a camera-to-world rotation C, so world_dir = C @ cam_dir.
* For a live frame and a reference view taken from the same point (pure rotation),
  bearings satisfy b_live = R @ b_ref with R = C_live.T @ C_ref, hence
  C_live = C_ref @ R.T. That is how the live heading is recovered.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import cv2
import numpy as np

# Internal verification parameters. The thresholds that decide LOCKED (N_MIN,
# GRID_CELLS_MIN) live in config.py's Part 3 block; these only shape the evidence.
SIFT_RATIO = 0.75  # Lowe ratio test (doc fallback: SIFT, ratio 0.75)
RANSAC_PX = 5.0  # homography reprojection threshold, pixels at working size
ROT_TOL_DEG = 3.0  # max angular residual for a match to count as rotation-consistent
GRID = 3  # inlier spread is measured on a GRID x GRID partition of the live frame
GRID_CELL_MIN_PTS = 2  # a cell counts as covered when it holds this many inliers
MIN_MATCHES = 8  # below this RANSAC is not attempted
LG_CACHE_MAX = 256  # reference views whose LightGlue device tensors stay cached

# COCO classes that move or change appearance. Their boxes are masked before
# embedding and matching so crowds and moved furniture cannot drive localization.
MOVABLE_CLASSES = frozenset(
    {
        "person", "chair", "couch", "bench", "dining table", "potted plant",
        "backpack", "handbag", "suitcase", "umbrella", "bicycle", "motorcycle",
        "dog", "cat", "bottle", "cup", "wine glass", "laptop", "cell phone",
        "book", "tv", "remote", "keyboard", "mouse", "teddy bear", "sports ball",
    }
)


# ---------------------------------------------------------------- angles / camera
def wrap_deg(a: float) -> float:
    """Wrap an angle to [0, 360)."""
    return float(a) % 360.0


def signed_ang_diff(a: float, b: float) -> float:
    """a - b wrapped to [-180, 180)."""
    return (float(a) - float(b) + 180.0) % 360.0 - 180.0


def ang_diff(a: float, b: float) -> float:
    """Absolute angular difference in degrees, in [0, 180]."""
    return abs(signed_ang_diff(a, b))


def focal_px(w: int, hfov_deg: float) -> float:
    return (w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def intrinsics(w: int, h: int, hfov_deg: float) -> np.ndarray:
    """Pinhole intrinsics for a w x h image whose horizontal FOV is hfov_deg."""
    f = focal_px(w, hfov_deg)
    return np.array([[f, 0.0, (w - 1) / 2.0], [0.0, f, (h - 1) / 2.0], [0.0, 0.0, 1.0]])


def cam_to_world(yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0) -> np.ndarray:
    """Camera-to-world rotation for a camera with the given yaw, pitch and roll."""
    psi, th, ph = (math.radians(v) for v in (yaw_deg, pitch_deg, roll_deg))
    fwd = np.array([math.sin(psi) * math.cos(th), math.sin(th), math.cos(psi) * math.cos(th)])
    right = np.array([math.cos(psi), 0.0, -math.sin(psi)])
    up = np.array([-math.sin(psi) * math.sin(th), math.cos(th), -math.cos(psi) * math.sin(th)])
    r2 = math.cos(ph) * right + math.sin(ph) * up
    u2 = -math.sin(ph) * right + math.cos(ph) * up
    return np.column_stack([r2, -u2, fwd])


def ypr_from_cam_to_world(C: np.ndarray) -> tuple[float, float, float]:
    """Inverse of cam_to_world: (yaw in [0, 360), pitch, roll) in degrees."""
    fwd = C[:, 2]
    yaw = math.degrees(math.atan2(fwd[0], fwd[2]))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, fwd[1]))))
    psi, th = math.radians(yaw), math.radians(pitch)
    right = np.array([math.cos(psi), 0.0, -math.sin(psi)])
    up = np.array([-math.sin(psi) * math.sin(th), math.cos(th), -math.cos(psi) * math.sin(th)])
    r2 = C[:, 0]
    roll = math.degrees(math.atan2(float(r2 @ up), float(r2 @ right)))
    return wrap_deg(yaw), pitch, roll


def pixel_dirs_cam(w: int, h: int, hfov_deg: float) -> np.ndarray:
    """(h, w, 3) camera-frame ray directions (z = 1) for every pixel centre."""
    K = intrinsics(w, h, hfov_deg)
    xs = (np.arange(w, dtype=np.float64) - K[0, 2]) / K[0, 0]
    ys = (np.arange(h, dtype=np.float64) - K[1, 2]) / K[1, 1]
    gx, gy = np.meshgrid(xs, ys)
    return np.dstack([gx, gy, np.ones_like(gx)])


def bearings(pts: np.ndarray, K: np.ndarray) -> np.ndarray:
    """Unit bearing vectors in the camera frame for (N, 2) pixel coordinates."""
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    x = (pts[:, 0] - K[0, 2]) / K[0, 0]
    y = (pts[:, 1] - K[1, 2]) / K[1, 1]
    b = np.column_stack([x, y, np.ones_like(x)])
    return b / np.linalg.norm(b, axis=1, keepdims=True)


def kabsch(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Rotation R minimizing sum ||dst - R src||^2 over unit vectors (N, 3)."""
    M = dst.T @ src
    U, _, Vt = np.linalg.svd(M)
    d = np.sign(np.linalg.det(U @ Vt)) or 1.0
    return U @ np.diag([1.0, 1.0, d]) @ Vt


def angular_residual_deg(R: np.ndarray, b_src: np.ndarray, b_dst: np.ndarray) -> np.ndarray:
    pred = b_src @ R.T
    cos = np.clip(np.sum(pred * b_dst, axis=1), -1.0, 1.0)
    return np.degrees(np.arccos(cos))


# ---------------------------------------------------------------- contrast
def normalize_contrast(img_rgb: np.ndarray) -> np.ndarray:
    """CLAHE on the L channel. Applied identically to references and live frames."""
    lab = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def to_gray(img_rgb: np.ndarray) -> np.ndarray:
    return img_rgb if img_rgb.ndim == 2 else cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)


# ---------------------------------------------------------------- masking
def _det_fields(det) -> tuple[str, Sequence[float]]:
    if isinstance(det, dict):
        return str(det.get("cls", "")), det.get("bbox", (0, 0, 0, 0))
    return str(getattr(det, "cls", "")), getattr(det, "bbox", (0, 0, 0, 0))


def movable_boxes_px(
    detections: Iterable, w: int, h: int, classes: frozenset = MOVABLE_CLASSES, pad_frac: float = 0.01
) -> list[tuple[int, int, int, int]]:
    """Pixel boxes (x1, y1, x2, y2), padded slightly, for detections of movable classes."""
    boxes = []
    for det in detections or ():
        cls, bb = _det_fields(det)
        if cls not in classes or len(bb) != 4:
            continue
        x1, y1, x2, y2 = (float(v) for v in bb)
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        px, py = pad_frac * w, pad_frac * h
        boxes.append(
            (
                max(0, int(math.floor(x1 * w - px))),
                max(0, int(math.floor(y1 * h - py))),
                min(w, int(math.ceil(x2 * w + px))),
                min(h, int(math.ceil(y2 * h + py))),
            )
        )
    return boxes


def keep_outside_boxes(kpts: np.ndarray, boxes: Sequence[tuple[int, int, int, int]]) -> np.ndarray:
    """Boolean mask of keypoints that lie outside every box."""
    kpts = np.asarray(kpts, dtype=np.float64).reshape(-1, 2)
    keep = np.ones(len(kpts), dtype=bool)
    for x1, y1, x2, y2 in boxes:
        inside = (kpts[:, 0] >= x1) & (kpts[:, 0] < x2) & (kpts[:, 1] >= y1) & (kpts[:, 1] < y2)
        keep &= ~inside
    return keep


def detection_mask(w: int, h: int, boxes: Sequence[tuple[int, int, int, int]]) -> np.ndarray | None:
    """uint8 mask, 255 = usable, 0 = movable object. None when there is nothing to mask."""
    if not boxes:
        return None
    m = np.full((h, w), 255, np.uint8)
    for x1, y1, x2, y2 in boxes:
        m[y1:y2, x1:x2] = 0
    return m


def blank_boxes(img_rgb: np.ndarray, boxes: Sequence[tuple[int, int, int, int]]) -> np.ndarray:
    """Fill movable-object boxes with the image median colour (used before embedding)."""
    if not boxes:
        return img_rgb
    out = img_rgb.copy()
    fill = np.median(img_rgb.reshape(-1, img_rgb.shape[-1]), axis=0).astype(img_rgb.dtype)
    for x1, y1, x2, y2 in boxes:
        out[y1:y2, x1:x2] = fill
    return out


# ---------------------------------------------------------------- features
@dataclass
class Features:
    kpts: np.ndarray  # (N, 2) float32 pixel coordinates (pixel-centre origin)
    desc: np.ndarray  # (N, D)
    size: tuple[int, int]  # (w, h)
    kind: str
    cache: dict = field(default_factory=dict, repr=False)  # matcher-specific state

    def __len__(self) -> int:
        return len(self.kpts)


class SiftMatcher:
    """OpenCV SIFT with Lowe's ratio test (0.75): the documented fallback matcher.

    Nearest neighbours come from exact brute-force L2 by default. The doc names FLANN;
    at our sizes (~1000 x 1000 descriptors) brute force measured 2x faster (8.6 vs
    17.8 ms) and, unlike FLANN's randomized kd-trees, it is deterministic, so the
    evaluation reproduces its numbers exactly. index="flann" restores FLANN.
    """

    name = "sift"

    def __init__(self, nfeatures: int = 1500, ratio: float = SIFT_RATIO, index: str = "bf"):
        self.sift = cv2.SIFT_create(nfeatures=nfeatures)
        self.ratio = ratio
        self.index = index
        self._bf = cv2.BFMatcher(cv2.NORM_L2)

    def extract(self, gray: np.ndarray, mask: np.ndarray | None = None) -> Features:
        kps, desc = self.sift.detectAndCompute(gray, mask)
        h, w = gray.shape[:2]
        if desc is None or not kps:
            return Features(np.zeros((0, 2), np.float32), np.zeros((0, 128), np.float32), (w, h), self.name)
        pts = np.array([k.pt for k in kps], dtype=np.float32)
        return Features(pts, desc.astype(np.float32), (w, h), self.name)

    def subset(self, f: Features, keep: np.ndarray) -> Features:
        return Features(f.kpts[keep], f.desc[keep], f.size, f.kind)

    def _knn(self, query: Features, train: Features):
        if self.index == "flann":
            m = train.cache.get("flann")
            if m is None:
                m = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=64))
                m.add([train.desc])
                m.train()
                train.cache["flann"] = m
            return m.knnMatch(query.desc, k=2)
        return self._bf.knnMatch(query.desc, train.desc, k=2)

    def match(self, query: Features, train: Features) -> tuple[np.ndarray, np.ndarray]:
        if len(query) < 2 or len(train) < 2:
            return np.zeros(0, int), np.zeros(0, int)
        qi, ti = [], []
        for pair in self._knn(query, train):
            if len(pair) == 2 and pair[0].distance < self.ratio * pair[1].distance:
                qi.append(pair[0].queryIdx)
                ti.append(pair[0].trainIdx)
        return np.asarray(qi, int), np.asarray(ti, int)


class LightGlueMatcher:
    """SuperPoint keypoints matched with LightGlue (cvg/LightGlue). Primary matcher."""

    name = "superpoint+lightglue"

    def __init__(self, max_keypoints: int = 1024, device: str | None = None):
        import torch
        from lightglue import LightGlue, SuperPoint

        self.torch = torch
        self.device = device or pick_device()
        self.extractor = SuperPoint(max_num_keypoints=max_keypoints).eval().to(self.device)
        self.matcher = LightGlue(features="superpoint").eval().to(self.device)
        # Reference features keep their device tensors between frames, but only the most recently
        # matched LG_CACHE_MAX of them: thousands of reference views at ~1 MB each would fill the GPU.
        self._lru: OrderedDict[int, Features] = OrderedDict()

    def _feats(self, kpts, desc, scores, size):
        t = self.torch
        return {
            "keypoints": t.from_numpy(kpts)[None].to(self.device),
            # descriptors may be stored as float16 to halve RAM; LightGlue runs in float32
            "descriptors": t.from_numpy(np.ascontiguousarray(desc, dtype=np.float32))[None].to(self.device),
            "keypoint_scores": t.from_numpy(scores)[None].to(self.device),
            "image_size": t.tensor([size], dtype=t.float32, device=self.device),
        }

    def extract(self, gray: np.ndarray, mask: np.ndarray | None = None) -> Features:
        t = self.torch
        h, w = gray.shape[:2]
        img = t.from_numpy(gray).float().div(255.0)[None].to(self.device)
        with t.inference_mode():
            out = self.extractor.extract(img, resize=None)
        kpts = out["keypoints"][0].float().cpu().numpy().astype(np.float32)
        desc = out["descriptors"][0].float().cpu().numpy().astype(np.float32)
        scores = out["keypoint_scores"][0].float().cpu().numpy().astype(np.float32)
        if mask is not None and len(kpts):
            xi = np.clip(np.round(kpts[:, 0]).astype(int), 0, w - 1)
            yi = np.clip(np.round(kpts[:, 1]).astype(int), 0, h - 1)
            keep = mask[yi, xi] > 0
            kpts, desc, scores = kpts[keep], desc[keep], scores[keep]
        f = Features(kpts, desc, (w, h), self.name)
        f.cache["scores"] = scores
        return f

    def subset(self, f: Features, keep: np.ndarray) -> Features:
        g = Features(f.kpts[keep], f.desc[keep], f.size, f.kind)
        g.cache["scores"] = f.cache["scores"][keep]
        return g

    def _lg(self, f: Features, track: bool = False):
        """Device tensors for f, cached on f. track=True (reference features) puts f in the LRU;
        the live frame's tensors are not tracked and go away with the frame."""
        d = f.cache.get("lg")
        if d is None:
            d = self._feats(f.kpts, f.desc, f.cache["scores"], f.size)
            f.cache["lg"] = d
        if track:
            k = id(f)  # the LRU holds f itself, so the id stays unique while it is listed
            self._lru[k] = f
            self._lru.move_to_end(k)
            while len(self._lru) > LG_CACHE_MAX:
                _, old = self._lru.popitem(last=False)
                old.cache.pop("lg", None)
        return d

    def match(self, query: Features, train: Features) -> tuple[np.ndarray, np.ndarray]:
        if len(query) < 2 or len(train) < 2:
            return np.zeros(0, int), np.zeros(0, int)
        with self.torch.inference_mode():
            out = self.matcher({"image0": self._lg(query), "image1": self._lg(train, track=True)})
        m = out["matches"][0].cpu().numpy()
        return m[:, 0].astype(int), m[:, 1].astype(int)


def pick_device() -> str:
    import os

    forced = os.environ.get("P3_DEVICE")
    if forced:
        return forced
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def make_matcher(name: str = "auto") -> tuple[object, list[str]]:
    """Build the local-feature matcher. 'auto' tries LightGlue, then falls back to SIFT.

    Returns (matcher, notes) where notes explain any fallback taken, for NOTES-part3.md.
    """
    notes: list[str] = []
    name = (name or "auto").lower()
    if name in ("sift", "sift-flann"):
        return SiftMatcher(index="flann" if name == "sift-flann" else "bf"), notes
    if name not in ("auto", "lightglue", "superpoint+lightglue"):
        raise ValueError(f"unknown matcher {name!r}")
    probe = np.zeros((64, 64), np.uint8)
    cv2.circle(probe, (32, 32), 10, 255, -1)
    preferred = pick_device()
    last: Exception | None = None
    for device in dict.fromkeys([preferred, "cpu"]):
        try:
            m = LightGlueMatcher(device=device)
            m.extract(probe)  # one forward pass catches mps and weight problems early
            if device != preferred:
                notes.append(f"LightGlue failed on {preferred} ({type(last).__name__}); using cpu")
            return m, notes
        except Exception as e:  # noqa: BLE001 - any failure means "take the fallback"
            last = e
    if name != "auto":
        raise RuntimeError(f"LightGlue unavailable: {last}") from last
    notes.append(f"LightGlue unavailable ({type(last).__name__}: {last}); fallback SIFT (ratio 0.75)")
    return SiftMatcher(), notes


def extract_features(matcher, img_rgb_norm: np.ndarray, boxes=()) -> Features:
    """Keypoints on a contrast-normalized frame, with movable-object boxes excluded.

    Detection is suppressed inside the boxes (mask) and any keypoint that still falls
    inside one is dropped, so nothing on a person or chair reaches the matcher.
    """
    gray = to_gray(img_rgb_norm)
    h, w = gray.shape[:2]
    feats = matcher.extract(gray, detection_mask(w, h, boxes))
    if boxes and len(feats):
        keep = keep_outside_boxes(feats.kpts, boxes)
        if not keep.all():
            feats = matcher.subset(feats, keep)
    return feats


# ---------------------------------------------------------------- verification
@dataclass
class Verification:
    n_matches: int = 0
    h_inliers: int = 0  # homography RANSAC inliers
    inliers: int = 0  # homography inliers that are also consistent with a pure rotation
    grid_cells: int = 0  # cells of the GRID x GRID live-frame partition holding inliers
    R: np.ndarray | None = None  # b_live = R @ b_ref
    resid_med_deg: float = float("nan")
    ms: float = 0.0
    live_pts: np.ndarray | None = field(default=None, repr=False)
    ref_pts: np.ndarray | None = field(default=None, repr=False)
    # Every putative match (before RANSAC). The rotation-consistent inliers above drop exactly the
    # parallax that position estimation needs, so PnP starts from these instead.
    match_live_pts: np.ndarray | None = field(default=None, repr=False)
    match_ref_pts: np.ndarray | None = field(default=None, repr=False)
    match_ref_idx: np.ndarray | None = field(default=None, repr=False)  # ref keypoint index per match

    def passes(self, n_min: int, grid_cells_min: int) -> bool:
        return self.inliers >= n_min and self.grid_cells >= grid_cells_min


def grid_coverage(pts: np.ndarray, w: int, h: int, grid: int = GRID, min_pts: int = GRID_CELL_MIN_PTS) -> int:
    if len(pts) == 0:
        return 0
    cx = np.clip((pts[:, 0] / w * grid).astype(int), 0, grid - 1)
    cy = np.clip((pts[:, 1] / h * grid).astype(int), 0, grid - 1)
    counts = np.bincount(cy * grid + cx, minlength=grid * grid)
    return int(np.sum(counts >= min_pts))


def verify(
    matcher,
    live: Features,
    ref: Features,
    K_live: np.ndarray,
    K_ref: np.ndarray,
    ransac_px: float = RANSAC_PX,
    rot_tol_deg: float = ROT_TOL_DEG,
) -> Verification:
    """Match live against ref, RANSAC a homography, then keep the inliers that a pure
    rotation explains. The user stands at the scan point, so a genuine match is almost
    a pure rotation; views from another scan point of the same wall fit a homography
    but leave parallax that a rotation cannot absorb, so they lose inliers here.
    """
    t0 = time.perf_counter()
    v = Verification()
    qi, ti = matcher.match(live, ref)
    v.n_matches = int(len(qi))
    if v.n_matches < MIN_MATCHES:
        v.ms = (time.perf_counter() - t0) * 1000
        return v
    p_live = live.kpts[qi].astype(np.float64)
    p_ref = ref.kpts[ti].astype(np.float64)
    v.match_live_pts, v.match_ref_pts, v.match_ref_idx = p_live, p_ref, np.asarray(ti)
    H, mask = cv2.findHomography(
        p_ref, p_live, cv2.RANSAC, ransac_px, maxIters=2000, confidence=0.999
    )
    if H is None or mask is None:
        v.ms = (time.perf_counter() - t0) * 1000
        return v
    hin = mask.ravel().astype(bool)
    v.h_inliers = int(hin.sum())
    if v.h_inliers < 3:
        v.ms = (time.perf_counter() - t0) * 1000
        return v
    b_live = bearings(p_live, K_live)
    b_ref = bearings(p_ref, K_ref)
    R = kabsch(b_ref[hin], b_live[hin])
    res = angular_residual_deg(R, b_ref, b_live)
    rin = hin & (res < rot_tol_deg)
    if rin.sum() >= 3:  # refit on the rotation-consistent set, then re-score
        R = kabsch(b_ref[rin], b_live[rin])
        res = angular_residual_deg(R, b_ref, b_live)
        rin = hin & (res < rot_tol_deg)
    v.R = R
    v.inliers = int(rin.sum())
    v.resid_med_deg = float(np.median(res[rin])) if v.inliers else float("nan")
    w, h = live.size
    v.grid_cells = grid_coverage(p_live[rin], w, h)
    v.live_pts, v.ref_pts = p_live[rin], p_ref[rin]
    v.ms = (time.perf_counter() - t0) * 1000
    return v


def live_pose(v: Verification, ref_yaw: float, ref_pitch: float, ref_roll: float = 0.0):
    """(yaw, pitch, roll) of the live camera in the pano frame, from a verification."""
    if v.R is None:
        return None
    C_live = cam_to_world(ref_yaw, ref_pitch, ref_roll) @ v.R.T
    return ypr_from_cam_to_world(C_live)


def estimate_hfov(
    pairs: Sequence[tuple[np.ndarray, np.ndarray, np.ndarray]],
    live_size: tuple[int, int],
    lo: float = 40.0,
    hi: float = 80.0,
    step: float = 0.25,
) -> tuple[float, float]:
    """Estimate the live camera's horizontal FOV from verified correspondences.

    pairs: (live_pts, ref_pts, K_ref) per frame, inliers of a verified match. With the
    wrong focal length a pure rotation cannot explain the correspondences, so the
    median angular residual is minimized at the true HFOV. Returns (hfov, residual).
    """
    w, h = live_size
    best = (float("nan"), float("inf"))
    for hfov in np.arange(lo, hi + 1e-9, step):
        K = intrinsics(w, h, float(hfov))
        res = []
        for live_pts, ref_pts, K_ref in pairs:
            if len(live_pts) < 5:
                continue
            bl, br = bearings(live_pts, K), bearings(ref_pts, K_ref)
            R = kabsch(br, bl)
            res.append(np.median(angular_residual_deg(R, br, bl)))
        if res:
            score = float(np.mean(res))
            if score < best[1]:
                best = (float(hfov), score)
    return best
