from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from server.detect import Detector


class Tensor:
    def __init__(self, array):
        self.array = np.asarray(array)

    def cpu(self):
        return self

    def numpy(self):
        return self.array


def install_fake_model(monkeypatch, fail_mps=False):
    import sys

    instances = []

    class Model:
        def __init__(self, path):
            self.path = path
            self.calls = []
            self.moves = []
            instances.append(self)

        def to(self, device):
            self.moves.append(device)

        def predict(self, **kwargs):
            self.calls.append(kwargs)
            if fail_mps and kwargs["device"] == "mps":
                raise RuntimeError("MPS unavailable")
            return [
                SimpleNamespace(
                    names={0: "person"},
                    boxes=SimpleNamespace(
                        xyxyn=Tensor([[-0.001, 0.1, 1.001, 0.9]]),
                        cls=Tensor([0]),
                        conf=Tensor([0.95]),
                    ),
                )
            ]

    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: fail_mps))
        ),
    )
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=Model))
    return instances


def test_offline_startup_never_attempts_missing_model_download(settings, monkeypatch):
    instances = install_fake_model(monkeypatch)
    with pytest.raises(RuntimeError, match="download-models"):
        Detector(settings)
    assert not instances


def test_rgb_input_warmup_normalized_boxes_and_cpu_fallback(settings, monkeypatch):
    instances = install_fake_model(monkeypatch, fail_mps=True)
    (settings.data_dir / "models").mkdir()
    (settings.data_dir / "models/yolo11n.pt").touch()
    detector = Detector(settings)
    assert detector.device == "cpu" and instances[0].moves == ["cpu"]
    rgb = np.full((64, 48, 3), [255, 0, 0], dtype=np.uint8)
    found = detector.detect(rgb)
    assert len(instances[0].calls) == 3  # failed MPS warm-up, CPU warm-up, actual image
    image = instances[0].calls[-1]["source"]
    assert isinstance(image, Image.Image) and image.getpixel((0, 0)) == (255, 0, 0)
    assert found[0].bbox == (0, 0.1, 1, 0.9) and found[0].cls == "person"


def test_fallback_weights_are_cached_and_explicit(settings, monkeypatch):
    install_fake_model(monkeypatch)
    (settings.data_dir / "models").mkdir()
    (settings.data_dir / "models/yolov8n.pt").touch()
    detector = Detector(replace(settings, yolo_device="cpu"))
    assert detector.model_path.name == "yolov8n.pt"
