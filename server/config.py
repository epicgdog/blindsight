"""Environment-backed configuration. Each team edits only its own marked block."""

import os
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
LOCALIZER = os.getenv("LOCALIZER", "stub")


@dataclass(frozen=True)
class Settings:
    port: int = PORT
    hfov_deg: float = HFOV_DEG
    yolo_conf: float = YOLO_CONF
    yolo_imgsz: int = YOLO_IMGSZ
    embed_every_n_frames: int = EMBED_EVERY_N_FRAMES
    cooldown_s: float = COOLDOWN_S
    min_gap_s: float = MIN_GAP_S
    record: bool = RECORD
    localizer: str = LOCALIZER
    detector: str = os.getenv("DETECTOR", "yolo")
    yolo_device: str = os.getenv("YOLO_DEVICE", "auto")
    yolo_model: str = os.getenv("YOLO_MODEL", "yolo11n.pt")
    allow_model_download: bool = env_bool("ALLOW_MODEL_DOWNLOAD")
    data_dir: Path = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve()
    viewer_dir: Path = ROOT / "server" / "viewer"
    pano_id: str | None = os.getenv("PANO_ID") or None
    pano_label: str | None = os.getenv("PANO_LABEL") or None
    max_message_bytes: int = 4 * 1024 * 1024
    max_image_pixels: int = 12_000_000
    viewer_fps: float = 10.0
    metrics_window_s: float = 60.0
    send_timeout_s: float = 2.0

    def __post_init__(self):
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


# --- end part 1 ---

# --- part 2 ---
# Reserved for phone/viewer configuration owned by Part 2.
# --- end part 2 ---

# --- part 3 ---
# Reserved for Part 3's calibration and localization thresholds.
# --- end part 3 ---
