"""YOLO inference on upright RGB arrays, with explicit offline startup."""

import argparse
import logging
from pathlib import Path

import numpy as np
from PIL import Image

from .config import Settings
from .protocol import Detection

log = logging.getLogger(__name__)


class StubDetector:
    device = "stub"
    model_path = None

    def detect(self, frame_rgb) -> list[Detection]:
        return []


class Detector:
    def __init__(self, settings: Settings | None = None):
        import torch
        from ultralytics import YOLO

        self.settings = settings or Settings()
        self.device = self.settings.yolo_device
        if self.device == "auto":
            self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        model_dir = self.settings.data_dir / "models"
        requested = Path(self.settings.yolo_model)
        primary = requested if requested.is_absolute() else model_dir / requested
        candidates = [primary]
        if requested.name == "yolo11n.pt":
            candidates.append(model_dir / "yolov8n.pt")
        self.model = None
        for path in candidates:
            if not path.is_file() and not self.settings.allow_model_download:
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                self.model = YOLO(str(path))
                self.model_path = path
                self.detect(np.zeros((640, 480, 3), dtype=np.uint8))
                if path != primary:
                    log.warning("Using fallback YOLO weights: %s", path.name)
                break
            except Exception:
                self.model = None
                log.warning("Unable to load/warm up %s", path.name)
        if self.model is None:
            raise RuntimeError(
                "No usable cached YOLO weights. Run `make download-models` online first."
            )

    def _predict(self, frame_rgb):
        # PIL conveys RGB explicitly; numpy inputs to Ultralytics are interpreted as BGR.
        return self.model.predict(
            source=Image.fromarray(frame_rgb),
            conf=self.settings.yolo_conf,
            imgsz=self.settings.yolo_imgsz,
            device=self.device,
            verbose=False,
            save=False,
        )[0]

    def detect(self, frame_rgb) -> list[Detection]:
        try:
            result = self._predict(frame_rgb)
        except RuntimeError:
            if self.device != "mps":
                raise
            log.warning("MPS inference failed; retrying on CPU")
            self.device = "cpu"
            self.model.to("cpu")
            result = self._predict(frame_rgb)
        boxes = result.boxes
        if boxes is None:
            return []
        coords = boxes.xyxyn.cpu().numpy().clip(0, 1)
        classes = boxes.cls.cpu().numpy().astype(int)
        scores = boxes.conf.cpu().numpy()
        return [
            Detection(result.names[int(cls)], float(score), tuple(float(v) for v in box))
            for cls, score, box in zip(classes, scores, coords, strict=True)
        ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    detector = Detector(Settings(allow_model_download=args.download))
    print(f"PASS YOLO loaded and warmed: {detector.model_path} ({detector.device})")


if __name__ == "__main__":
    main()
