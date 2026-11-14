"""Part 3 Localizer: is the live frame at the locked pano, and which way does it face?

Implements the contract from Part 1's protocol.py exactly:

    class Localizer:
        def update(self, frame_rgb, detections, heading_deg) -> Location
        def add_reference(self, frame_rgb) -> bool   # warm-up: verify, then add

Pipeline for every frame (Blindsight Part 3 doc, Phase 4):
 1. Mask movable things: boxes of person, chair, bags, ... (geometry.MOVABLE_CLASSES)
    are blanked before embedding and excluded from keypoints before matching.
 2. Retrieve (coarse): global embedding (DINOv2-small, fallbacks open_clip ViT-B-32
    then a weight-free HOG descriptor) against the reference views; top 3 proposals.
    A bag of visual words over the frame's local features adds 2 proposals and picks
    the competing-place views (BowIndex). Retrieval only proposes candidates and
    never decides LOCKED.
 3. Verify with geometry: local features (SuperPoint+LightGlue, fallback SIFT+FLANN),
    RANSAC homography, keep inliers a pure rotation explains, require >= N_MIN inliers
    spread over >= GRID_CELLS_MIN of 9 grid cells. Competing places (views rendered
    from the other scan points of the same model) are verified too, and the locked
    pano must beat the best of them by PLACE_RATIO. Without this, a view of the same
    wall from 3 m away passes the single-pano check (measured, see NOTES-part3.md).
 4. Heading: rotation between live frame and the best reference, plus the
    reference's yaw, gives yaw_deg; yaw_conf follows the evidence.
 5. Fuse over time (LocStateMachine): LOCKED when 2 of the last 3 frames match with
    yaw agreeing within YAW_AGREE_DEG, LOST after 5 consecutive failures, yaw carried
    by the phone heading between fixes, visual jumps > YAW_JUMP_DEG need 2 frames.

score is the retrieval cosine similarity of the best verified candidate (T_LO gates
candidates; T_HI is the retrieval-only threshold used by the ablation). inliers is
the geometric evidence. One pano gives the scan point and heading, not a metric
position, so nothing finer than "at this spot, facing yaw" is claimed.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from localization_debug import localization_reason  # noqa: E402
from matchviz import footprint_polygon, rank_places  # noqa: E402
from refmask import compute_masks, mask_key, region_polygons  # noqa: E402
from geometry import (  # noqa: E402
    Features,
    ang_diff,
    blank_boxes,
    extract_features,
    intrinsics,
    live_pose,
    make_matcher,
    movable_boxes_px,
    normalize_contrast,
    pick_device,
    to_gray,
    verify,
    wrap_deg,
)
from refs import (  # noqa: E402
    DATA_DIR,
    REF_SIZE,
    CubeSampler,
    RefView,
    build_refs,
    load_faces,
    load_layout,
    load_pano_meta,
    load_refs,
)

# Part 1's contract types. Loaded as server.localize (Part 1's server), they come from
# server.protocol, as HANDOFF.md asks; loaded as a script or by the tools, from the same
# file as a top-level module; before Part 1 is merged, identical local shapes are used.
Detection = Location = None  # type: ignore[assignment,misc]
try:
    if __package__ == "server":
        from .protocol import Detection, Location  # type: ignore  # noqa: F401
    else:
        from protocol import Detection, Location  # type: ignore  # noqa: F401
except Exception:  # noqa: BLE001
    Detection = Location = None  # type: ignore[assignment,misc]
if Location is None:

    @dataclass
    class Detection:  # type: ignore[no-redef]
        cls: str
        conf: float
        bbox: tuple  # normalized x1, y1, x2, y2

    @dataclass
    class Location:  # type: ignore[no-redef]
        pano_id: str | None
        label: str | None
        state: str  # LOCKED, UNCERTAIN, LOST
        score: float
        inliers: int
        yaw_deg: float | None
        yaw_conf: float
        best_ref: str | None


LOCKED, UNCERTAIN, LOST = "LOCKED", "UNCERTAIN", "LOST"
TOPK_DEMO = 3  # global-retrieval proposals from the locked pano (doc: top 3)
TOPK_DEMO_BOW = 2  # extra proposals from the local-feature vocabulary
TOPK_COMP = 3  # proposals from competing scan points (vocabulary)
MAX_COMPETITORS = int(os.environ.get("P3_MAX_COMPETITORS", "20"))
# Multi-place mode (every pano is a candidate place): a new place must beat the best other place by
# this inlier ratio, and be seen on this many consecutive frames, before the reported place moves.
MULTI_MARGIN = float(os.environ.get("P3_MULTI_MARGIN", "1.15"))
SWITCH_FRAMES = int(os.environ.get("P3_SWITCH_FRAMES", "2"))
# IMU gate: a verified match whose pitch disagrees with the phone accelerometer by more than this
# many degrees is not trusted (gravity gives pitch to a few degrees; vision can lock onto look-alikes).
PITCH_GATE_DEG = float(os.environ.get("P3_PITCH_GATE_DEG", "15"))
MAX_ONSITE_NEG = 200  # frames from Part 1's POST /api/capture/neg used as competing views
ONSITE_NEG = "onsite_negatives"  # place name for those frames
COMP_YAWS = tuple(range(0, 360, 15))  # competitor views: every 15 degrees at pitch 0
MAX_ONSITE = 150  # cap on warm-up references


# ---------------------------------------------------------------- settings
@dataclass
class Settings:
    """Part 3 thresholds plus the two Part 1 base values Part 3 reads.

    Precedence per field: config.py (Part 1 file; Part 3 owns its marked block, which
    itself reads env and data/calibration.json) > environment > calibration.json >
    default. Defaults are the safe, uncalibrated values: retrieval never decides
    (T_HI > 1), every candidate is verified (T_LO < -1), N_MIN starts at 30.
    """

    T_HI: float = 1.01
    T_LO: float = -1.01
    N_MIN: int = 30
    GRID_CELLS_MIN: int = 3
    YAW_AGREE_DEG: float = 15.0
    YAW_JUMP_DEG: float = 40.0
    PLACE_RATIO: float = 1.5
    LOCK_NEED: int = 2  # 2 of the last 3 frames; fallback "require 3 of 3" sets 3
    HFOV_DEG: float = 55.0  # Part 1 base config
    EMBED_EVERY_N_FRAMES: int = 3  # Part 1 base config
    LOST_AFTER: int = 5
    matcher: str = "auto"
    retriever: str = "auto"
    calibrated: bool = False
    source: dict = field(default_factory=dict)

    NUMERIC = ("T_HI", "T_LO", "N_MIN", "GRID_CELLS_MIN", "YAW_AGREE_DEG", "YAW_JUMP_DEG",
               "PLACE_RATIO", "LOCK_NEED", "HFOV_DEG", "EMBED_EVERY_N_FRAMES", "LOST_AFTER")

    @classmethod
    def load(cls, calibration: dict | str | Path | None = None, use_config: bool = True,
             use_env: bool = True) -> "Settings":
        s = cls()
        cal: dict = {}
        if isinstance(calibration, dict):
            cal = calibration
        else:
            p = Path(calibration) if calibration else DATA_DIR / "calibration.json"
            if p.exists():
                cal = json.loads(p.read_text())
        cfg = None
        if use_config:
            try:
                import config as cfg  # type: ignore
            except Exception:  # noqa: BLE001
                cfg = None
        for name in cls.NUMERIC:
            typ = type(getattr(s, name))
            if cfg is not None and hasattr(cfg, name):
                setattr(s, name, typ(getattr(cfg, name)))
                s.source[name] = "config.py"
            elif use_env and os.environ.get(name) is not None:
                setattr(s, name, typ(os.environ[name]))
                s.source[name] = "env"
            elif name in cal:
                setattr(s, name, typ(cal[name]))
                s.source[name] = "calibration.json"
        s.matcher = os.environ.get("P3_MATCHER") if use_env and os.environ.get("P3_MATCHER") else cal.get("matcher", "auto")
        s.retriever = os.environ.get("P3_RETRIEVER") if use_env and os.environ.get("P3_RETRIEVER") else cal.get("retriever", "auto")
        s.calibrated = bool(cal.get("T_HI") is not None and cal.get("N_MIN") is not None)
        return s

    def thresholds(self) -> dict:
        return {k: getattr(self, k) for k in self.NUMERIC} | {"calibrated": self.calibrated}


# ---------------------------------------------------------------- retrieval
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float32)


def _offline() -> bool:
    return os.environ.get("P3_OFFLINE") == "1" or os.environ.get("HF_HUB_OFFLINE") == "1"


class Dinov2Embedder:
    """DINOv2-small via transformers: L2(concat(L2 CLS, L2 mean patch token))."""

    name = "dinov2-small"
    MODEL_ID = "facebook/dinov2-small"

    def __init__(self, model=None, device: str | None = None):
        import torch

        self.torch = torch
        self.device = device or pick_device()
        if model is None:
            from transformers import AutoModel

            try:
                model = AutoModel.from_pretrained(self.MODEL_ID, local_files_only=True)
            except Exception:  # noqa: BLE001
                if _offline():
                    raise
                model = AutoModel.from_pretrained(self.MODEL_ID)
        self.model = model.eval().to(self.device)

    def _tensor(self, imgs: list[np.ndarray]):
        h0, w0 = imgs[0].shape[:2]
        W = 224
        H = max(14, int(round(W * h0 / w0 / 14.0)) * 14)
        arr = np.stack([cv2.resize(i, (W, H), interpolation=cv2.INTER_AREA) for i in imgs]).astype(np.float32) / 255.0
        arr = (arr - IMAGENET_MEAN) / IMAGENET_STD
        return self.torch.from_numpy(arr).permute(0, 3, 1, 2).contiguous().to(self.device)

    def embed_batch(self, imgs: list[np.ndarray], bs: int = 16) -> np.ndarray:
        F = self.torch.nn.functional
        out = []
        for i in range(0, len(imgs), bs):
            with self.torch.inference_mode():
                hs = self.model(pixel_values=self._tensor(imgs[i:i + bs])).last_hidden_state
                cls = F.normalize(hs[:, 0].float(), dim=-1)
                patch = F.normalize(hs[:, 1:].float().mean(1), dim=-1)
                out.append(F.normalize(self.torch.cat([cls, patch], -1), dim=-1).cpu().numpy())
        return np.concatenate(out).astype(np.float32)


class OpenClipEmbedder:
    """open_clip ViT-B-32 image embedding (documented retrieval fallback)."""

    name = "openclip-vit-b-32"

    def __init__(self, device: str | None = None, pretrained: str = "laion2b_s34b_b79k"):
        import open_clip
        import torch
        from PIL import Image

        self.torch, self.Image = torch, Image
        self.device = device or pick_device()
        model, _, self.preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained=pretrained)
        self.model = model.eval().to(self.device)

    def embed_batch(self, imgs: list[np.ndarray], bs: int = 32) -> np.ndarray:
        out = []
        for i in range(0, len(imgs), bs):
            x = self.torch.stack([self.preprocess(self.Image.fromarray(im)) for im in imgs[i:i + bs]]).to(self.device)
            with self.torch.inference_mode():
                v = self.model.encode_image(x).float()
            out.append(self.torch.nn.functional.normalize(v, dim=-1).cpu().numpy())
        return np.concatenate(out).astype(np.float32)


class ClassicEmbedder:
    """Weight-free last resort: gradient-orientation histograms on a 4x4 grid plus
    coarse colour. Weaker than DINOv2, but retrieval only proposes candidates, so a
    weak retriever costs recall, never precision."""

    name = "classic-hog"

    def _one(self, img: np.ndarray) -> np.ndarray:
        g = cv2.resize(to_gray(img), (96, 128), interpolation=cv2.INTER_AREA).astype(np.float32)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        mag, ang = cv2.cartToPolar(gx, gy)
        b = (np.mod(ang, np.pi) / np.pi * 9).astype(int) % 9
        cy = np.minimum(np.arange(128) // 32, 3)[:, None].repeat(96, 1)
        cx = np.minimum(np.arange(96) // 24, 3)[None, :].repeat(128, 0)
        hist = np.bincount(((cy * 4 + cx) * 9 + b).ravel(), weights=mag.ravel(), minlength=144).astype(np.float32)
        hist = np.sqrt(hist)
        hist /= np.linalg.norm(hist) + 1e-9
        lab = cv2.cvtColor(cv2.resize(img, (6, 8), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2LAB).astype(np.float32)
        col = ((lab[:, :, 1:] - 128.0) / 64.0).ravel()
        col /= np.linalg.norm(col) + 1e-9
        v = np.concatenate([hist, 0.5 * col])
        return v / (np.linalg.norm(v) + 1e-9)

    def embed_batch(self, imgs: list[np.ndarray], bs: int = 0) -> np.ndarray:
        return np.stack([self._one(i) for i in imgs]).astype(np.float32)


def make_embedder(name: str = "auto") -> tuple[object, list[str]]:
    """Retriever in the documented order: DINOv2-small, open_clip ViT-B-32, classic."""
    name = (name or "auto").lower()
    builders = {"dinov2-small": Dinov2Embedder, "openclip-vit-b-32": OpenClipEmbedder, "classic-hog": ClassicEmbedder}
    aliases = {"dinov2": "dinov2-small", "openclip": "openclip-vit-b-32", "open_clip": "openclip-vit-b-32",
               "classic": "classic-hog"}
    name = aliases.get(name, name)
    if name != "auto":
        return builders[name](), []
    notes = []
    for key in ("dinov2-small", "openclip-vit-b-32"):
        try:
            e = builders[key]()
            e.embed_batch([np.zeros((64, 48, 3), np.uint8)])
            return e, notes
        except Exception as ex:  # noqa: BLE001
            notes.append(f"retriever {key} unavailable ({type(ex).__name__}: {str(ex)[:120]})")
    notes.append("retriever fallback: classic-hog (weight-free); retrieval proposes only")
    return ClassicEmbedder(), notes


def prefetch_weights() -> dict:
    """Download every model weight Part 3 can use, then prove each loads offline."""
    report = {}
    os.environ.pop("P3_OFFLINE", None)
    for key, fn in (("dinov2-small", Dinov2Embedder), ("superpoint+lightglue", lambda: make_matcher("lightglue")[0])):
        try:
            fn()
            report[key] = "downloaded"
        except Exception as ex:  # noqa: BLE001
            report[key] = f"FAILED: {type(ex).__name__}: {ex}"
    os.environ["P3_OFFLINE"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        Dinov2Embedder()
        report["dinov2-small offline"] = "PASS"
    except Exception as ex:  # noqa: BLE001
        report["dinov2-small offline"] = f"FAIL: {type(ex).__name__}"
    try:  # torch.hub caches LightGlue/SuperPoint weights under $TORCH_HOME
        make_matcher("lightglue")
        report["superpoint+lightglue offline"] = "PASS"
    except Exception as ex:  # noqa: BLE001
        report["superpoint+lightglue offline"] = f"FAIL: {type(ex).__name__}"
    return report


# ---------------------------------------------------------------- local-feature vocabulary
class BowIndex:
    """Bag of visual words over the local features the matcher already extracts.

    A second, weight-free proposal source next to the global retriever. It picks the
    competing-place views to verify (and two extra locked-pano views), so a weak global
    retriever cannot hide the view that would expose a hard negative. Deterministic:
    fixed-seed k-means on a fixed-seed descriptor sample.
    """

    def __init__(self, feats: list[Features], k: int = 1024, sample: int = 30000, iters: int = 12, seed: int = 0):
        rng = np.random.default_rng(seed)
        allD = np.concatenate([f.desc for f in feats if len(f)]).astype(np.float32)
        D = allD[rng.choice(len(allD), min(sample, len(allD)), replace=False)]
        k = int(min(k, max(8, len(D) // 20)))
        C = D[rng.choice(len(D), k, replace=False)].copy()
        for _ in range(iters):
            a = self._assign_to(D, C)
            for j in range(k):
                m = a == j
                if m.any():
                    C[j] = D[m].mean(0)
        self.C = C
        self.c2 = (C * C).sum(1)
        tf = np.stack([self._tf(f) for f in feats])
        self.idf = np.log((len(feats) + 1) / (1 + (tf > 0).sum(0))).astype(np.float32)
        self.M = self._norm(tf * self.idf)

    @staticmethod
    def _assign_to(D: np.ndarray, C: np.ndarray) -> np.ndarray:
        return np.argmin((C * C).sum(1)[None] - 2.0 * D @ C.T, axis=1)

    def _tf(self, f: Features) -> np.ndarray:
        if not len(f):
            return np.zeros(len(self.C) if hasattr(self, "C") else 0, np.float32)
        w = np.argmin(self.c2[None] - 2.0 * f.desc.astype(np.float32) @ self.C.T, axis=1)
        return np.bincount(w, minlength=len(self.C)).astype(np.float32)

    @staticmethod
    def _norm(x: np.ndarray) -> np.ndarray:
        x = np.sqrt(x)
        return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-9)

    def query(self, f: Features) -> np.ndarray:
        return self.M @ self._norm(self._tf(f) * self.idf)

    def add(self, f: Features) -> None:
        self.M = np.vstack([self.M, self._norm(self._tf(f) * self.idf)[None]])


# ---------------------------------------------------------------- state machine
@dataclass
class Obs:
    matched: bool
    yaw: float | None
    heading: float | None
    frame: int = 0


@dataclass
class StepResult:
    state: str
    yaw: float | None
    conf: float
    accepted: bool  # a visual yaw fix was accepted on this frame
    pending_jump: bool  # a large visual jump is waiting for a second frame


class LocStateMachine:
    """Temporal fusion of per-frame verified matches (Phase 4, step 5)."""

    def __init__(self, agree_deg: float = 15.0, jump_deg: float = 40.0, lost_after: int = 5,
                 window: int = 3, need: int = 2):
        self.agree_deg, self.jump_deg = agree_deg, jump_deg
        self.lost_after, self.window, self.need = lost_after, window, need
        self.reset()

    def reset(self) -> None:
        self.hist: deque[Obs] = deque(maxlen=self.window)
        self.fails = 0
        self.fix: tuple[float, float | None] | None = None  # (yaw, heading at fix)
        self.pending: Obs | None = None
        self.conf = 0.0
        self.since_fix = 0
        self.state = UNCERTAIN
        self.n = 0

    def predicted_yaw(self, heading: float | None) -> float | None:
        if self.fix is None:
            return None
        y, h = self.fix
        if heading is not None and h is not None:
            return wrap_deg(y + (heading - h))
        return y

    def _agree(self, a: Obs, b: Obs) -> bool:
        if a.heading is not None and b.heading is not None:
            return ang_diff(a.yaw - a.heading, b.yaw - b.heading) <= self.agree_deg
        return ang_diff(a.yaw, b.yaw) <= self.agree_deg

    def _locked(self) -> bool:
        m = [o for o in self.hist if o.matched and o.yaw is not None]
        for combo in itertools.combinations(m, self.need) if len(m) >= self.need else ():
            if all(self._agree(a, b) for a, b in itertools.combinations(combo, 2)):
                return True
        return False

    def step(self, matched: bool, yaw: float | None, heading: float | None, conf: float = 1.0) -> StepResult:
        self.n += 1
        pred = self.predicted_yaw(heading)
        accepted = pending = False
        obs = Obs(False, None, heading, self.n)
        if matched and yaw is not None:
            cand = Obs(True, wrap_deg(yaw), heading, self.n)
            if self.pending is not None and self.n - self.pending.frame > self.window:
                self.pending = None
            if pred is None or ang_diff(cand.yaw, pred) <= self.jump_deg:
                accepted, obs = True, cand
                self.pending = None
            elif self.pending is not None and self._agree(self.pending, cand):
                accepted, obs = True, cand  # two frames confirm the jump
                self.pending.matched = True
                self.pending = None
            else:  # unconfirmed jump: hold it, do not count it as a verified match yet
                obs = Obs(False, cand.yaw, heading, self.n)
                self.pending = obs
                pending = True
        self.hist.append(obs)
        if accepted:
            self.fails, self.fix, self.conf, self.since_fix = 0, (obs.yaw, heading), float(conf), 0
        else:
            self.fails += 1
            self.since_fix += 1
        if self._locked():
            state = LOCKED
        elif self.fails >= self.lost_after:
            state = LOST
        else:
            state = UNCERTAIN
        self.state = state
        if state == LOST:
            self.fix = self.pending = None
            self.conf = 0.0
            return StepResult(state, None, 0.0, accepted, pending)
        out_yaw = obs.yaw if accepted else self.predicted_yaw(heading)
        decay = 0.85 if heading is not None else 0.6
        out_conf = self.conf * (decay ** self.since_fix) if out_yaw is not None else 0.0
        return StepResult(state, out_yaw, float(out_conf), accepted, pending)


# ---------------------------------------------------------------- per-frame evaluation
@dataclass
class Cand:
    idx: int
    name: str
    place: str
    demo: bool
    sim: float
    inliers: int = 0
    grid: int = 0
    h_inliers: int = 0
    n_matches: int = 0
    yaw: float | None = None
    pitch: float | None = None
    roll: float | None = None
    ms: float = 0.0


@dataclass
class FrameEval:
    cands: list[Cand]
    top_demo: Cand | None  # retrieval top-1 among locked-pano references
    top_any: Cand | None  # retrieval top-1 over every reference (ablation)
    embed_ms: float = 0.0
    extract_ms: float = 0.0
    verify_ms: float = 0.0
    total_ms: float = 0.0
    sims: np.ndarray | None = field(default=None, repr=False)  # not serialized

    def best_demo(self, t_lo: float = -2.0) -> Cand | None:
        c = [c for c in self.cands if c.demo and c.sim >= t_lo]
        return max(c, key=lambda c: (c.inliers, c.grid)) if c else None

    def best_comp_inliers(self) -> int:
        return max((c.inliers for c in self.cands if not c.demo), default=0)

    def decide(self, s: Settings, mode: str = "geometry", margin: float | None = None) -> tuple[bool, Cand | None]:
        if mode == "retrieval":
            t = self.top_any
            ok = t is not None and t.demo and t.sim >= s.T_HI
            return ok, (t if ok else self.top_demo)
        b = self.best_demo(s.T_LO)
        if b is None:
            return False, self.top_demo
        ok = b.inliers >= s.N_MIN and b.grid >= s.GRID_CELLS_MIN
        comp = self.best_comp_inliers()
        if ok and comp > 0 and b.inliers < s.PLACE_RATIO * comp:
            ok = False
        if ok and margin:  # several candidate places: the best must clearly beat the runner-up
            other = max((c.inliers for c in self.cands if c.demo and c.place != b.place), default=0)
            if other > 0 and b.inliers < margin * other:
                ok = False
        return ok, b

    def to_json(self) -> dict:
        return {
            "cands": [asdict(c) for c in self.cands],
            "top_demo": asdict(self.top_demo) if self.top_demo else None,
            "top_any": asdict(self.top_any) if self.top_any else None,
            "embed_ms": self.embed_ms, "extract_ms": self.extract_ms,
            "verify_ms": self.verify_ms, "total_ms": self.total_ms,
        }

    @classmethod
    def from_json(cls, d: dict) -> "FrameEval":
        mk = lambda x: Cand(**x) if x else None  # noqa: E731
        return cls([Cand(**c) for c in d["cands"]], mk(d["top_demo"]), mk(d["top_any"]),
                   d["embed_ms"], d["extract_ms"], d["verify_ms"], d["total_ms"])


@dataclass
class _Ref:
    view: RefView
    place: str
    demo: bool
    feats: Features
    K: np.ndarray
    masks: list = field(default_factory=list)  # movable-object boxes (px) masked out of this view


# ---------------------------------------------------------------- the Localizer
class Localizer:
    """Real localizer behind Part 1's contract (config flag LOCALIZER=real)."""

    def __init__(self, data_dir: str | Path | None = None, settings: Settings | None = None,
                 matcher=None, embedder=None, competitors="auto", mode: str = "geometry",
                 pano_id: str | None = None, ref_size: tuple[int, int] = REF_SIZE, verbose: bool = True,
                 ref_detector=None, ref_detector_id: str = "detector", places=None):
        t0 = time.perf_counter()
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.s = settings or Settings.load(self.data_dir / "calibration.json")
        self.mode = mode
        self.ref_size = tuple(ref_size)
        self.verbose = verbose
        # Optional detector (image -> detections) used to mask people and furniture out of the
        # pano reference views, the same way the live frame is masked.
        self.ref_detector = ref_detector
        self.ref_detector_id = ref_detector_id
        self.notes: list[str] = []
        meta = load_pano_meta(self.data_dir)
        self.pano_id = pano_id or meta["pano_id"]
        self.label = meta.get("label") or "this spot"
        self._meta = meta
        if matcher is None:
            matcher, n = make_matcher(self.s.matcher)
            self.notes += n
        if embedder is None:
            embedder, n = make_embedder(self.s.retriever)
            self.notes += n
        self.matcher, self.embedder = matcher, embedder
        if self.s.calibrated and self.s.matcher not in ("auto", matcher.name):
            self.notes.append(f"WARNING calibration was done with matcher {self.s.matcher}, running {matcher.name}")
        if self.s.calibrated and self.s.retriever not in ("auto", embedder.name):
            self.notes.append(f"WARNING calibration was done with retriever {self.s.retriever}, running {embedder.name}")

        self._refs: list[_Ref] = []
        self._load_place(self.pano_id, demo=True)
        # places: None = one candidate place (the configured pano); "all" or N = also treat the
        # other panos on disk (nearest N) as candidate places, so we report which one we are at.
        competitors_arg = competitors
        self.candidate_places: list[str] = [self.pano_id]
        for pid in self._candidate_ids(meta, places):
            try:
                self._load_place(pid, demo=True)
                self.candidate_places.append(pid)
            except FileNotFoundError as ex:
                self.notes.append(f"candidate place {pid} skipped: {ex}")
        self.multi = len(self.candidate_places) > 1
        self._place: str | None = None  # the place currently reported (multi mode)
        self._votes: dict[str, int] = {}
        self.competitors: list[str] = []
        if self.multi and competitors == "auto":
            competitors = []  # every pano is a candidate already
        for cid in self._competitor_ids(meta, competitors):
            try:
                self._load_place(cid, demo=False)
                self.competitors.append(cid)
            except FileNotFoundError as ex:
                self.notes.append(f"competitor {cid} skipped: {ex}")
        if competitors_arg == "auto" or (isinstance(competitors, (list, tuple)) and ONSITE_NEG in competitors):
            self._load_onsite_negatives()
        self._emb = self._embed_refs([r for r in self._refs])
        self._bow = BowIndex([r.feats for r in self._refs])
        self._is_demo = np.array([r.demo for r in self._refs])
        self._is_pano_demo = np.array([r.demo and r.view.source == "pano" for r in self._refs])
        self._sm = LocStateMachine(self.s.YAW_AGREE_DEG, self.s.YAW_JUMP_DEG, self.s.LOST_AFTER, 3, self.s.LOCK_NEED)
        self._lock = threading.RLock()
        self._n = 0
        self._sims: np.ndarray | None = None
        self.n_onsite = 0
        self.last_debug: dict = {}
        self.startup_s = time.perf_counter() - t0
        if verbose:
            print(f"[localize] {self.info_line()} startup {self.startup_s:.1f}s", file=sys.stderr)
            for n in self.notes:
                print(f"[localize] {n}", file=sys.stderr)

    # -- loading -------------------------------------------------------------
    def _place_positions(self, meta: dict) -> dict[str, tuple[float, float, float]]:
        pos: dict[str, tuple[float, float, float]] = {}
        f = self.data_dir / "matterport" / "panoLocations.json"
        if f.is_file():
            try:
                def walk(v):
                    if isinstance(v, dict):
                        if "id" in v and isinstance(v.get("position"), dict):
                            p = v["position"]
                            pos[v["id"]] = (float(p["x"]), float(p["y"]), float(p["z"]))
                        for c in v.values():
                            walk(c)
                    elif isinstance(v, list):
                        for c in v:
                            walk(c)
                walk(json.loads(f.read_text()))
            except (OSError, ValueError, KeyError, TypeError):
                pass
        for n in meta.get("negatives", []):
            p = n.get("position")
            if p and n["pano_id"] not in pos:
                pos[n["pano_id"]] = (float(p["x"]), float(p["y"]), float(p["z"]))
        if meta.get("position") and self.pano_id not in pos:
            p = meta["position"]
            pos[self.pano_id] = (float(p["x"]), float(p["y"]), float(p["z"]))
        return pos

    def _candidate_ids(self, meta: dict, places) -> list[str]:
        """Other panos on disk to treat as candidate places, nearest first."""
        if places in (None, False, 0, "none", "1", 1):
            return []
        root = self.data_dir / "pano"
        ids = [d.name for d in sorted(root.iterdir())
               if d.is_dir() and d.name != self.pano_id and (d / "face_1.jpg").exists()]
        if isinstance(places, (list, tuple)):
            ids = [p for p in places if p != self.pano_id and p in ids]
        pos = self._place_positions(meta)
        here = np.array(pos.get(self.pano_id, (0.0, 0.0, 0.0)))
        ids.sort(key=lambda i: float(np.linalg.norm(np.array(pos[i]) - here)) if i in pos else 1e9)
        if isinstance(places, str) and places.isdigit():
            places = int(places)
        if isinstance(places, int):
            ids = ids[: max(0, places - 1)]  # places counts the configured pano too
        return ids

    def place_label(self, pid: str) -> str:
        """A speakable name for a place: room name, else the scan number, else the pano label."""
        if pid == self.pano_id:
            return self.label
        for n in self._meta.get("negatives", []):
            if n.get("pano_id") == pid and n.get("room"):
                return str(n["room"])
        f = self.data_dir / "matterport" / "panoLocations.json"
        if f.is_file():
            try:
                lab = None

                def walk(v):
                    nonlocal lab
                    if isinstance(v, dict):
                        if v.get("id") == pid and v.get("label") is not None:
                            lab = v["label"]
                        for c in v.values():
                            walk(c)
                    elif isinstance(v, list):
                        for c in v:
                            walk(c)
                walk(json.loads(f.read_text()))
                if lab is not None:
                    return f"scan point {lab}"
            except (OSError, ValueError):
                pass
        return self.label

    def _competitor_ids(self, meta: dict, competitors) -> list[str]:
        if competitors in (None, "none", False, 0):
            return []
        if competitors != "auto":
            return [c for c in competitors if c not in (self.pano_id, ONSITE_NEG) and c not in self.candidate_places]
        negs = sorted(meta.get("negatives", []), key=lambda n: n.get("dist_m", 1e9))
        ids = [n["pano_id"] for n in negs if n["pano_id"] not in self.candidate_places
               and (self.data_dir / "pano" / n["pano_id"] / "face_1.jpg").exists()]
        return ids[:MAX_COMPETITORS]

    def _load_place(self, place: str, demo: bool) -> None:
        pdir = self.data_dir / "pano" / place
        views: list[RefView] | None = None
        rdir = pdir / "refs"
        if demo and (rdir / "refs.json").exists():
            views, meta = load_refs(rdir)
            if abs(meta.get("hfov", -1) - self.s.HFOV_DEG) > 1e-6 or tuple(meta.get("size", ())) != self.ref_size:
                self.notes.append(f"refs on disk built for hfov {meta.get('hfov')} size {meta.get('size')}; rebuilding")
                views = None
        if views is None:
            faces = load_faces(pdir)
            sampler = CubeSampler(faces, load_layout(pdir))
            if demo:
                views = build_refs(sampler, self.s.HFOV_DEG, self.ref_size)
            else:
                views = build_refs(sampler, self.s.HFOV_DEG, self.ref_size, yaws=COMP_YAWS, pitches=(0,))
        masks = self._masks_for(pdir, views)
        feats = self._cached_features(pdir, views, masks)
        for v, f in zip(views, feats):
            w, h = v.image.shape[1], v.image.shape[0]
            self._refs.append(_Ref(v, place, demo, f, intrinsics(w, h, v.hfov), list(masks.get(v.name, ()))))

    def _load_onsite_negatives(self) -> None:
        """Frames captured away from the scan point (Part 1: POST /api/capture/neg ->
        data/negatives/) become competing views: a live frame that matches one of them at
        least as well as the pano is not at the scan point. Their pose is unknown and
        unused; only their inlier counts matter. Disable with P3_ONSITE_NEGATIVES=0."""
        d = self.data_dir / "negatives"
        if os.environ.get("P3_ONSITE_NEGATIVES") == "0" or not d.is_dir():
            return
        files = sorted(d.glob("*.jpg"))[-MAX_ONSITE_NEG:]
        for f in files:
            img = cv2.imread(str(f), cv2.IMREAD_COLOR)
            if img is None:
                continue
            raw = self._prep(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
            h, w = raw.shape[:2]
            view = RefView(f"neg_{f.stem}", 0.0, 0.0, raw, "onsite_neg", 0.0, self.s.HFOV_DEG)
            feats = extract_features(self.matcher, normalize_contrast(raw))
            self._refs.append(_Ref(view, ONSITE_NEG, False, feats, intrinsics(w, h, self.s.HFOV_DEG)))
        if files:
            self.competitors.append(ONSITE_NEG)
            self.notes.append(f"{len(files)} on-site negative frames used as competing views")

    def _masks_for(self, pdir: Path, views: list[RefView]) -> dict:
        if self.ref_detector is None:
            return {}
        masks = compute_masks(pdir / "cache", views, self.ref_detector, mask_key(self.ref_detector_id, 0))
        n = sum(len(b) for b in masks.values())
        self.notes.append(f"{pdir.name}: masked {n} movable objects in {len(views)} reference views")
        return masks

    def _cached_features(self, pdir: Path, views: list[RefView], masks: dict | None = None) -> list[Features]:
        """Reference keypoints, cached per place/matcher/size (LightGlue on CPU is slow)."""
        w, h = self.ref_size
        masks = masks or {}
        tag = ""
        if any(masks.values()):  # masked features must not share a cache with unmasked ones
            tag = "_m" + mask_key(json.dumps({k: masks[k] for k in sorted(masks)}), 0)
        key = f"feats_{self.matcher.name.replace('+', '_')}_{w}x{h}_{self.s.HFOV_DEG:g}{tag}.npz"
        cache = pdir / "cache" / key
        names = [v.name for v in views]
        if cache.exists():
            try:
                z = np.load(cache, allow_pickle=False)
                if list(z["names"]) == names:
                    out = []
                    for i in range(len(names)):
                        f = Features(z[f"k{i}"], z[f"d{i}"], (w, h), self.matcher.name)
                        if f"s{i}" in z:
                            f.cache["scores"] = z[f"s{i}"]
                        out.append(f)
                    return out
            except Exception:  # noqa: BLE001 - a bad cache is just recomputed
                pass
        out = [extract_features(self.matcher, normalize_contrast(v.image), masks.get(v.name) or ()) for v in views]
        try:
            arrs = {"names": np.array(names)}
            for i, f in enumerate(out):
                arrs[f"k{i}"], arrs[f"d{i}"] = f.kpts, f.desc
                if "scores" in f.cache:
                    arrs[f"s{i}"] = f.cache["scores"]
            cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez(cache, **arrs)
        except OSError:
            pass
        return out

    def _embed_imgs(self, refs: list[_Ref]) -> np.ndarray:
        imgs = []
        for r in refs:
            img = normalize_contrast(r.view.image)
            imgs.append(blank_boxes(img, r.masks) if r.masks else img)
        return self.embedder.embed_batch(imgs)

    def _embed_refs(self, refs: list[_Ref]) -> np.ndarray:
        """Embeddings for every reference. Pano views are cached per place (embedding thousands of
        views is slow); on-site frames and other non-pano references are embedded fresh."""
        out: list[np.ndarray | None] = [None] * len(refs)
        groups: dict[str, list[int]] = {}
        for i, r in enumerate(refs):
            if r.view.source == "pano" and r.place != ONSITE_NEG:
                groups.setdefault(r.place, []).append(i)
        for place, idx in groups.items():
            group = [refs[i] for i in idx]
            names = [r.view.name for r in group]
            tag = mask_key(json.dumps([names, [r.masks for r in group]]), 0)
            cache = self.data_dir / "pano" / place / "cache" / f"emb_{self.embedder.name}_{tag}.npy"
            arr = None
            if cache.exists():
                try:
                    arr = np.load(cache, allow_pickle=False)
                    if arr.shape[0] != len(group):
                        arr = None
                except Exception:  # noqa: BLE001 - a bad cache is just recomputed
                    arr = None
            if arr is None:
                arr = self._embed_imgs(group)
                try:
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    np.save(cache, arr)
                except OSError:
                    pass
            for k, i in enumerate(idx):
                out[i] = arr[k]
        rest = [i for i, v in enumerate(out) if v is None]
        if rest:
            fresh = self._embed_imgs([refs[i] for i in rest])
            for k, i in enumerate(rest):
                out[i] = fresh[k]
        return np.vstack(out) if out else np.zeros((0, 1), np.float32)

    def masked_regions(self) -> list:
        """Masked people/furniture on the demo pano as equirect polygons (for the viewer)."""
        demo = [r for r in self._refs if r.demo and r.view.source == "pano" and r.masks]
        if not demo:
            return []
        h, w = demo[0].view.image.shape[:2]
        return region_polygons([r.view for r in demo], {r.view.name: r.masks for r in demo}, (w, h))

    # -- helpers ---------------------------------------------------------------
    def _prep(self, frame_rgb) -> np.ndarray:
        img = np.asarray(frame_rgb)
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        elif img.shape[2] == 4:
            img = img[:, :, :3]
        img = np.ascontiguousarray(img, dtype=np.uint8)
        w = self.ref_size[0]
        if img.shape[1] != w:
            h = max(1, int(round(img.shape[0] * w / img.shape[1])))
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        return img

    def _verify(self, i: int, sim: float, feats: Features, K_live: np.ndarray) -> Cand:
        r = self._refs[i]
        v = verify(self.matcher, feats, r.feats, K_live, r.K)
        c = Cand(i, r.view.name, r.place, r.demo, float(sim), v.inliers, v.grid_cells, v.h_inliers,
                 v.n_matches, ms=v.ms)
        pose = live_pose(v, r.view.yaw, r.view.pitch, r.view.roll)
        if pose is not None:
            c.yaw, c.pitch, c.roll = (round(float(x), 3) for x in pose)
        return c

    def _top(self, sims: np.ndarray, mask: np.ndarray, k: int) -> list[int]:
        idx = np.flatnonzero(mask)
        if len(idx) == 0:
            return []
        return [int(i) for i in idx[np.argsort(-sims[idx])[:k]]]

    def _yaw_neighbor(self, yaw: float) -> int | None:
        best, bd = None, 1e9
        place = (self._place if self.multi else None) or self.pano_id
        for i, r in enumerate(self._refs):
            if r.demo and r.view.source == "pano" and r.view.pitch == 0 and r.place == place:
                d = ang_diff(r.view.yaw, yaw)
                if d < bd:
                    best, bd = i, d
        return best

    def _cand_of(self, i: int, sims: np.ndarray) -> Cand:
        r = self._refs[i]
        return Cand(i, r.view.name, r.place, r.demo, float(sims[i]))

    # -- per-frame evaluation (also used by calibrate.py and eval_localization.py) ---
    def evaluate_frame(self, frame_rgb, detections=(), pano_refs_only: bool = False,
                       extra_demo: list[int] | None = None, sims: np.ndarray | None = None,
                       competitors: str = "always", verify_cands: bool = True) -> FrameEval:
        """Raw evidence for one frame, independent of thresholds except T_LO.

        competitors: "always" verifies competing places for every frame (calibration
        needs the raw numbers); "if_needed" only when the locked pano passes N_MIN.
        verify_cands=False skips local features entirely (retrieval-only ablation).
        """
        t0 = time.perf_counter()
        img = normalize_contrast(self._prep(frame_rgb))
        h, w = img.shape[:2]
        boxes = movable_boxes_px(detections, w, h)
        t1 = time.perf_counter()
        if sims is None:
            q = self.embedder.embed_batch([blank_boxes(img, boxes)])[0]
            sims = self._emb @ q
        t2 = time.perf_counter()
        demo_mask = self._is_pano_demo if pano_refs_only else self._is_demo
        top_demo_ids = self._top(sims, demo_mask, 1)
        top_any_ids = self._top(sims, demo_mask | ~self._is_demo, 1)
        demo_ids = [i for i in self._top(sims, demo_mask, TOPK_DEMO) if sims[i] >= self.s.T_LO]
        if not verify_cands:
            t3 = time.perf_counter()
            return FrameEval([self._cand_of(i, sims) for i in demo_ids],
                             self._cand_of(top_demo_ids[0], sims) if top_demo_ids else None,
                             self._cand_of(top_any_ids[0], sims) if top_any_ids else None,
                             embed_ms=(t2 - t1) * 1000, extract_ms=(t1 - t0) * 1000, verify_ms=0.0,
                             total_ms=(t3 - t0) * 1000, sims=sims)
        feats = extract_features(self.matcher, img, boxes)
        bow = self._bow.query(feats)
        for i in self._top(bow, demo_mask, TOPK_DEMO_BOW) + list(extra_demo or ()):
            if i is not None and i not in demo_ids:
                demo_ids.append(i)
        t3 = time.perf_counter()
        K_live = intrinsics(w, h, self.s.HFOV_DEG)
        cands = [self._verify(i, sims[i], feats, K_live) for i in demo_ids]
        best = max(cands, key=lambda c: (c.inliers, c.grid), default=None)
        run_comp = competitors == "always" or (
            best is not None and best.inliers >= self.s.N_MIN and best.grid >= self.s.GRID_CELLS_MIN)
        if run_comp and self.competitors:
            cands += [self._verify(i, sims[i], feats, K_live) for i in self._top(bow, ~self._is_demo, TOPK_COMP)]
        t4 = time.perf_counter()
        return FrameEval(
            cands,
            self._cand_of(top_demo_ids[0], sims) if top_demo_ids else None,
            self._cand_of(top_any_ids[0], sims) if top_any_ids else None,
            embed_ms=(t2 - t1) * 1000, extract_ms=(t3 - t2) * 1000 + (t1 - t0) * 1000,
            verify_ms=(t4 - t3) * 1000, total_ms=(t4 - t0) * 1000, sims=sims,
        )

    # -- contract ----------------------------------------------------------------
    def update(self, frame_rgb, detections, heading_deg, pitch_deg=None, roll_deg=None) -> Location:
        with self._lock:
            t0 = time.perf_counter()
            self._n += 1
            heading = None if heading_deg is None else float(heading_deg)
            n_every = max(1, int(self.s.EMBED_EVERY_N_FRAMES))
            embed_now = self._sims is None or (self._n - 1) % n_every == 0
            pred = self._sm.predicted_yaw(heading)
            extra = [self._yaw_neighbor(pred)] if pred is not None else []
            fe = self.evaluate_frame(frame_rgb, detections, extra_demo=extra,
                                     sims=None if embed_now else self._sims, competitors="if_needed",
                                     verify_cands=self.mode != "retrieval")
            if embed_now:
                self._sims = fe.sims
            matched, best = fe.decide(self.s, self.mode, MULTI_MARGIN if self.multi else None)
            imu_rejected = False
            if matched and best is not None and pitch_deg is not None and best.pitch is not None:
                if abs(float(best.pitch) - float(pitch_deg)) > PITCH_GATE_DEG:
                    matched, imu_rejected = False, True
            if self.multi:
                matched, best = self._hold_place(matched, best)
            yaw = None
            conf = 0.0
            if matched and best is not None:
                if self.mode == "retrieval":
                    yaw = self._refs[best.idx].view.yaw
                    conf = 0.3
                else:
                    yaw = best.yaw
                    conf = min(1.0, best.inliers / (2.0 * max(1, self.s.N_MIN)))
            st = self._sm.step(matched, yaw, heading, conf)
            ref = best or fe.top_demo
            pano_id, label = self.pano_id, self.label
            if self.multi:
                if st.state == LOST:
                    self._place, self._votes = None, {}
                pano_id = self._place or (ref.place if ref else self.pano_id)
                label = self.place_label(pano_id)
            loc = Location(
                pano_id=pano_id,
                label=label,
                state=st.state,
                score=round(float(ref.sim), 4) if ref else 0.0,
                inliers=int(best.inliers) if best else 0,
                yaw_deg=None if st.yaw is None else round(float(st.yaw), 1),
                yaw_conf=round(float(st.conf), 3),
                best_ref=ref.name if ref else None,
            )
            self.last_debug = {
                "match": self._match_view(fe, best, ref, st.yaw, frame_rgb.shape),
                "imu_rejected": imu_rejected,
                "imu_pitch": pitch_deg, "imu_roll": roll_deg,
                "matched": matched, "embed": round(fe.embed_ms, 1), "extract": round(fe.extract_ms, 1),
                "verify": round(fe.verify_ms, 1), "total": round((time.perf_counter() - t0) * 1000, 1),
                "candidates": [(c.name, c.place, c.inliers, c.grid) for c in fe.cands],
                "competitor_inliers": fe.best_comp_inliers(), "pending_jump": st.pending_jump,
                "embedded": embed_now,
                "reason": localization_reason(fe, self.s, matched, st.state),
                "scope": "selected_scan_only",
                "selected_pano_id": self.pano_id,
                "thresholds": self.s.thresholds(),
                "best_retrieval_score": float(ref.sim) if ref else None,
                "best_inliers": int(best.inliers) if best else 0,
                "best_grid_cells": int(best.grid) if best else 0,
                "frame_shape": list(frame_rgb.shape),
            }
            return loc

    def _hold_place(self, matched: bool, best):
        """Multi-place hysteresis: a different place must win on SWITCH_FRAMES frames in a row."""
        if not matched or best is None:
            self._votes = {}
            return matched, best
        place = best.place
        if self._place is None or place == self._place:
            self._place, self._votes = place, {}
            return True, best
        n = self._votes.get(place, 0) + 1
        self._votes = {place: n}
        if n >= SWITCH_FRAMES:
            self._place, self._votes = place, {}
            self._sm.reset()  # yaw history belongs to the old place
            return True, best
        return False, best  # hold the current place until the new one is confirmed

    def _match_view(self, fe, best, ref, yaw, shape) -> dict | None:
        """For the viewer: places ranked by retrieval score, and where the frame sits on the pano."""
        try:
            sims = fe.sims
            if sims is None:
                return None
            places = [r.place for r in self._refs][: len(sims)]
            inl: dict[str, int] = {}
            for c in fe.cands:
                inl[c.place] = max(inl.get(c.place, 0), int(c.inliers))
            ranking = rank_places(np.asarray(sims)[: len(places)], places, top=8, inliers=inl)
            footprint = None
            if best is not None and best.yaw is not None:
                h, w = shape[:2]
                pitch = best.pitch if best.pitch is not None else 0.0
                roll = best.roll if best.roll is not None else 0.0
                footprint = footprint_polygon(
                    yaw if yaw is not None else best.yaw, pitch, roll, self.s.HFOV_DEG, h / max(1, w)
                )
            return {"ranking": ranking, "footprint": footprint}
        except Exception:  # visualisation must never break localization
            return None

    def add_reference(self, frame_rgb) -> bool:
        """Warm-up: verify the frame against the pano views (not against other on-site
        references, so they cannot bootstrap off each other) and add it only if it
        passes the same test as a LOCKED frame, competitors included."""
        with self._lock:
            if self.n_onsite >= MAX_ONSITE:
                return False
            fe = self.evaluate_frame(frame_rgb, (), pano_refs_only=True, competitors="if_needed")
            ok, b = fe.decide(self.s, "geometry")
            if not ok or b is None or b.yaw is None:
                return False
            raw = self._prep(frame_rgb)
            norm = normalize_contrast(raw)
            self.n_onsite += 1
            view = RefView(f"onsite{self.n_onsite:03d}_y{int(round(b.yaw)) % 360:03d}", float(b.yaw),
                           float(b.pitch or 0.0), raw, "onsite", float(b.roll or 0.0), self.s.HFOV_DEG)
            feats = extract_features(self.matcher, norm)
            h, w = raw.shape[:2]
            self._refs.append(_Ref(view, self.pano_id, True, feats, intrinsics(w, h, self.s.HFOV_DEG)))
            self._emb = np.vstack([self._emb, self.embedder.embed_batch([norm])])
            self._bow.add(feats)
            self._is_demo = np.append(self._is_demo, True)
            self._is_pano_demo = np.append(self._is_pano_demo, False)
            self._sims = None
            return True

    # -- extras for Part 1 (self-check, /api/pano, eval) -------------------------
    def add_negative(self, frame_rgb) -> None:
        """Use a frame captured away from the scan point (POST /api/capture/neg) as a
        competing view right away, without a restart."""
        with self._lock:
            raw = self._prep(frame_rgb)
            norm = normalize_contrast(raw)
            h, w = raw.shape[:2]
            k = int((~self._is_demo).sum())
            view = RefView(f"neg_live{k:04d}", 0.0, 0.0, raw, "onsite_neg", 0.0, self.s.HFOV_DEG)
            feats = extract_features(self.matcher, norm)
            self._refs.append(_Ref(view, ONSITE_NEG, False, feats, intrinsics(w, h, self.s.HFOV_DEG)))
            self._emb = np.vstack([self._emb, self.embedder.embed_batch([norm])])
            self._bow.add(feats)
            self._is_demo = np.append(self._is_demo, False)
            self._is_pano_demo = np.append(self._is_pano_demo, False)
            if ONSITE_NEG not in self.competitors:
                self.competitors.append(ONSITE_NEG)
            self._sims = None

    def apply_settings(self, s: Settings) -> None:
        """Swap thresholds (e.g. raw evaluation -> calibrated) and reset temporal state."""
        with self._lock:
            self.s = s
            self._sm = LocStateMachine(s.YAW_AGREE_DEG, s.YAW_JUMP_DEG, s.LOST_AFTER, 3, s.LOCK_NEED)
            self._n = 0
            self._sims = None

    def reset(self) -> None:
        with self._lock:
            self._sm.reset()
            self._n = 0
            self._sims = None
            self._place, self._votes = None, {}

    def info(self) -> dict:
        return {
            "pano_id": self.pano_id, "label": self.label, "mode": self.mode,
            "candidate_places": self.candidate_places,
            "n_refs": int(self._is_pano_demo.sum()), "n_onsite": self.n_onsite,
            "competitors": self.competitors, "n_competitor_refs": int((~self._is_demo).sum()),
            "matcher": self.matcher.name, "retriever": self.embedder.name,
            "thresholds": self.s.thresholds(), "notes": self.notes,
        }

    def info_line(self) -> str:
        i = self.info()
        cal = "calibrated" if self.s.calibrated else "UNCALIBRATED defaults"
        return (f"pano {i['pano_id']} '{i['label']}', {i['n_refs']} refs, {len(i['competitors'])} competing places "
                f"({i['n_competitor_refs']} views), {i['matcher']} + {i['retriever']}, {cal}")


# ---------------------------------------------------------------- CLI
PHOTO_EXT = (".jpg", ".jpeg", ".png")


def try_photos(data_dir: Path, photo_dir: Path) -> int:
    """Run the localizer on real photos, each judged on its own (no temporal fusion,
    since photos in different directions would never agree on yaw). Prints, per photo,
    whether it matches the locked scan point, the evidence and the heading."""
    photo_dir = Path(photo_dir).expanduser()
    files = sorted(p for p in photo_dir.iterdir() if p.suffix.lower() in PHOTO_EXT)
    if not files:
        heic = [p for p in photo_dir.iterdir() if p.suffix.lower() in (".heic", ".heif")]
        hint = (" They are HEIC: convert them first with\n  mkdir -p jpg && sips -s format jpeg *.HEIC --out jpg/"
                if heic else "")
        print(f"no .jpg/.png photos in {photo_dir}.{hint}")
        return 2
    loc = Localizer(data_dir)
    print(f"{'photo':<26} {'match':<5} {'inliers':>7} {'grid':>4} {'rival':>5} {'yaw':>6}  {'best view':<12} {'ms':>5}")
    n_match = 0
    for f in files:
        img = cv2.imread(str(f), cv2.IMREAD_COLOR)  # applies the photo's EXIF rotation
        if img is None:
            print(f"{f.name[:26]:<26} unreadable")
            continue
        fe = loc.evaluate_frame(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), (), competitors="always")
        ok, b = fe.decide(loc.s)
        n_match += ok
        yaw = "-" if not (ok and b and b.yaw is not None) else f"{b.yaw:6.1f}"
        print(f"{f.name[:26]:<26} {'YES' if ok else 'no':<5} {b.inliers if b else 0:>7} {b.grid if b else 0:>4} "
              f"{fe.best_comp_inliers():>5} {yaw:>6}  {(b.name if b else '-'):<12} {fe.total_ms:5.0f}")
    print(f"\n{n_match} of {len(files)} photos matched the locked scan point "
          f"({loc.pano_id}, '{loc.label}'). 'rival' = best inliers from another scan point; "
          f"yaw is degrees in the pano frame.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Part 3 localizer utilities")
    ap.add_argument("--prefetch", action="store_true", help="download weights, confirm offline load")
    ap.add_argument("--selfcheck", action="store_true", help="load the localizer, print PASS/FAIL")
    ap.add_argument("--try", dest="try_dir", type=Path, default=None,
                    help="run the localizer on the photos in a folder, each on its own")
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    a = ap.parse_args(argv)
    if a.prefetch:
        rep = prefetch_weights()
        print(json.dumps(rep, indent=1))
        return 0 if all("FAIL" not in v for v in rep.values()) else 1
    if a.try_dir:
        return try_photos(a.data_dir, a.try_dir)
    if a.selfcheck:
        try:
            loc = Localizer(a.data_dir)
            print("PASS localizer loaded:", loc.info_line())
            print(("PASS" if loc.s.calibrated else "FAIL") + " calibration loaded")
            return 0 if loc.s.calibrated else 1
        except Exception as ex:  # noqa: BLE001
            print(f"FAIL localizer: {type(ex).__name__}: {ex}")
            return 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
