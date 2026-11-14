"""Environment-backed configuration. Each team edits only its own marked block."""

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

# --- part 1 ---
ROOT = Path(__file__).resolve().parents[1]


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(int(default))).lower()
    if value not in {"0", "1", "true", "false"}:
        raise ValueError(f"{name} must be 0, 1, true or false")
    return value in {"1", "true"}


PORT = int(os.getenv("PORT", "8000"))
HFOV_DEG = float(os.getenv("HFOV_DEG", "55"))
YOLO_CONF = float(os.getenv("YOLO_CONF", "0.35"))
YOLO_IMGSZ = int(os.getenv("YOLO_IMGSZ", "640"))
EMBED_EVERY_N_FRAMES = int(os.getenv("EMBED_EVERY_N_FRAMES", "3"))
COOLDOWN_S = float(os.getenv("COOLDOWN_S", "5"))
MIN_GAP_S = float(os.getenv("MIN_GAP_S", "1.5"))
RECORD = env_bool("RECORD")
# Demo Matterport model (Princeton - Rabinowitz Hall) and its start pano (label "0").
MATTERPORT_MODEL_ID = os.getenv("MATTERPORT_MODEL_ID", "P3S6VUTP3qB")
# Last-resort fallbacks: PANO_ID/PANO_LABEL, Part 3's pano.json and downloaded data win over these.
DEFAULT_PANO_ID = "1k478kza39tbfu1uhbttg5ybb"
DEFAULT_PANO_LABEL = "Rabinowitz Hall"
LOCALIZER = os.getenv("LOCALIZER", "stub")
CANDIDATE_PLACES = os.getenv("CANDIDATE_PLACES", "all")  # "all", a number N (nearest N), or "1"
MASK_REFS = env_bool("MASK_REFS", True)  # mask people/furniture out of the pano reference views


@dataclass(frozen=True)
class Settings:
    port: int = PORT
    hfov_deg: float = HFOV_DEG
    yolo_conf: float = YOLO_CONF
    yolo_imgsz: int = YOLO_IMGSZ
    embed_every_n_frames: int = EMBED_EVERY_N_FRAMES
    cooldown_s: float = COOLDOWN_S
    min_gap_s: float = MIN_GAP_S
    detailed_cues: bool = env_bool("DETAILED_CUES", True)
    record: bool = RECORD
    localizer: str = LOCALIZER
    mask_refs: bool = MASK_REFS
    candidate_places: str = CANDIDATE_PLACES
    detector: str = os.getenv("DETECTOR", "yolo")
    yolo_device: str = os.getenv("YOLO_DEVICE", "auto")
    yolo_model: str = os.getenv("YOLO_MODEL", "yolo11n.pt")
    allow_model_download: bool = env_bool("ALLOW_MODEL_DOWNLOAD")
    data_dir: Path = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
    viewer_dir: Path = ROOT / "server" / "viewer"
    matterport_model_id: str = MATTERPORT_MODEL_ID
    default_pano_id: str = DEFAULT_PANO_ID
    default_pano_label: str = DEFAULT_PANO_LABEL
    pano_id: str | None = os.getenv("PANO_ID") or None
    pano_label: str | None = os.getenv("PANO_LABEL") or None
    max_message_bytes: int = 4 * 1024 * 1024
    max_image_pixels: int = 50_000_000  # hard cap; phone photos are 12 MP and get downscaled
    max_frame_side: int = 1280  # longest side after decoding; larger frames are downscaled
    viewer_fps: float = 10.0
    metrics_window_s: float = 60.0
    send_timeout_s: float = 2.0

    def __post_init__(self):
        if not all(
            math.isfinite(v)
            for v in (
                self.hfov_deg,
                self.yolo_conf,
                self.cooldown_s,
                self.min_gap_s,
                self.viewer_fps,
                self.metrics_window_s,
                self.send_timeout_s,
            )
        ):
            raise ValueError("Numeric configuration must be finite")
        if self.localizer not in {"stub", "real"}:
            raise ValueError("LOCALIZER must be stub or real")
        if self.detector not in {"stub", "yolo"}:
            raise ValueError("DETECTOR must be stub or yolo")
        if not 0 < self.hfov_deg < 180 or not 0 <= self.yolo_conf <= 1:
            raise ValueError("Invalid field of view or confidence")
        if self.embed_every_n_frames < 1 or self.yolo_imgsz < 32:
            raise ValueError("Invalid inference frequency or image size")
        if self.cooldown_s < 0 or self.min_gap_s < 0.8:
            raise ValueError("Cooldown must be nonnegative; MIN_GAP_S must be >= 0.8")
        if not 0 < self.viewer_fps <= 10 or not 1 <= self.port <= 65535:
            raise ValueError("Invalid viewer frequency or port")
        if self.candidate_places != "all" and not self.candidate_places.isdigit():
            raise ValueError("CANDIDATE_PLACES must be 'all' or a number")
        if self.max_frame_side < 64:
            raise ValueError("max_frame_side must be at least 64")
        if self.metrics_window_s <= 0 or self.send_timeout_s <= 0:
            raise ValueError("Metrics window and send timeout must be positive")
        if not re.fullmatch(r"[A-Za-z0-9]{6,32}", self.matterport_model_id):
            raise ValueError("MATTERPORT_MODEL_ID must be 6-32 letters or digits")
        if any(
            p and (p in {".", ".."} or Path(p).name != p)
            for p in (self.pano_id, self.default_pano_id)
        ):
            raise ValueError("PANO_ID must be a directory name, not a path")


# --- end part 1 ---

# --- part 2 ---
# Reserved for phone/viewer configuration owned by Part 2.
# --- end part 2 ---

# --- part 3 ---
# Reserved for Part 3's calibration and localization thresholds.
# --- end part 3 ---
