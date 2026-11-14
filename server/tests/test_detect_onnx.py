"""DETECTOR=onnx: YOLO through OpenCV, for machines without PyTorch (Intel Macs)."""

import numpy as np
import pytest

from server import detect_onnx
from server.config import Settings
from server.detect_onnx import NAMES, OnnxDetector, decode, letterbox


def output(*boxes, classes=80):
    """Fake YOLOv8/11 output (1, 4 + classes, anchors): boxes as (cx, cy, w, h, cls, score)."""
    out = np.zeros((1, 4 + classes, max(len(boxes), 1)), np.float32)
    for k, (cx, cy, w, h, cls, score) in enumerate(boxes):
        out[0, :4, k] = (cx, cy, w, h)
        out[0, 4 + cls, k] = score
    return out


def test_coco_names_in_ultralytics_order():
    assert len(NAMES) == 80
    assert NAMES[0] == "person" and NAMES[13] == "bench" and NAMES[56] == "chair"
    assert NAMES[58] == "potted plant" and NAMES[60] == "dining table" and NAMES[62] == "tv"
    assert NAMES[72] == "refrigerator" and NAMES[79] == "toothbrush"


def test_letterbox_portrait_frame():
    image, scale, px, py = letterbox(np.zeros((640, 480, 3), np.uint8), 640)
    assert image.shape == (640, 640, 3) and scale == 1.0 and (px, py) == (80, 0)
    assert image[0, 0, 0] == 114 and image[0, 100, 0] == 0


def test_decode_undoes_letterbox_filters_and_suppresses_duplicates():
    out = output(
        (320, 320, 96, 128, 0, 0.9),  # a person in the middle of a 480 x 640 frame
        (322, 321, 96, 128, 0, 0.6),  # the same person again: removed
        (500, 100, 40, 40, 56, 0.2),  # below the confidence threshold: dropped
        (150, 500, 60, 80, 56, 0.7),  # a chair
    )
    found = decode(out, 1.0, 80, 0, 480, 640, 0.35)
    assert [d.cls for d in found] == ["person", "chair"]
    np.testing.assert_allclose(found[0].bbox, (0.4, 0.4, 0.6, 0.6), atol=1e-6)
    assert found[0].conf == pytest.approx(0.9)
    assert all(0 <= v <= 1 for d in found for v in d.bbox)


def test_decode_nothing_found():
    assert decode(output((320, 320, 50, 50, 0, 0.1)), 1.0, 0, 0, 640, 640, 0.35) == []


def test_detector_feeds_rgb_640_and_returns_detections(tmp_path, monkeypatch):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "yolo11n.onnx").write_bytes(b"fake")
    seen = []

    class Net:
        def setInput(self, blob):
            seen.append(blob)

        def forward(self):
            return output((320, 320, 96, 128, 62, 0.8))

    monkeypatch.setattr(detect_onnx.cv2.dnn, "readNetFromONNX", lambda path: Net())
    detector = OnnxDetector(Settings(detector="onnx", data_dir=tmp_path))
    frame = np.zeros((640, 480, 3), np.uint8)
    frame[..., 0] = 255  # red, in RGB order
    found = detector.detect(frame)
    blob = seen[-1]
    assert blob.shape == (1, 3, 640, 640)
    assert blob[0, 0, 320, 320] == pytest.approx(1.0) and blob[0, 2, 320, 320] == 0
    assert [d.cls for d in found] == ["tv"]


def test_missing_model_says_where_to_put_it(tmp_path):
    with pytest.raises(RuntimeError, match="yolo11n.onnx"):
        OnnxDetector(Settings(detector="onnx", data_dir=tmp_path))


def test_settings_and_server_choose_the_onnx_detector(tmp_path, monkeypatch):
    from server import server

    made = []
    monkeypatch.setattr(detect_onnx, "OnnxDetector", lambda settings: made.append(settings))
    settings = Settings(detector="onnx", data_dir=tmp_path)
    server.make_detector(settings)
    assert made == [settings]
    with pytest.raises(ValueError):
        Settings(detector="tensorflow")
