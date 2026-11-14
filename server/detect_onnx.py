"""YOLO through OpenCV's DNN module: object detection without PyTorch (DETECTOR=onnx).

PyTorch has no Intel-Mac build, so `make server` cannot run there. This reads the same
YOLO model exported to ONNX (data/models/yolo11n.onnx) and needs only numpy and
opencv-python-headless. It gives the same output as server.detect.Detector: Detection
objects with COCO class names and normalized boxes. Export once on a machine with
PyTorch:

  yolo export model=yolo11n.pt format=onnx imgsz=640 opset=12 simplify=True

Check it:  python -m server.detect_onnx [--image photo.jpg]
"""

import argparse
import logging
import time
from pathlib import Path

import cv2
import numpy as np

from .config import Settings
from .protocol import Detection

log = logging.getLogger(__name__)

# Ultralytics COCO class order (index = class id), the same names server.detect speaks.
NAMES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
)  # fmt: skip
NMS_IOU = 0.7  # Ultralytics' default
MAX_DET = 300


def model_path(settings: Settings) -> Path:
    requested = Path(settings.yolo_model).with_suffix(".onnx")
    return requested if requested.is_absolute() else settings.data_dir / "models" / requested.name


def letterbox(rgb, size):
    """Resize keeping the aspect ratio and pad to size x size with grey, like Ultralytics."""
    h, w = rgb.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = round(w * scale), round(h * scale)
    px, py = (size - nw) // 2, (size - nh) // 2
    canvas = np.full((size, size, 3), 114, np.uint8)
    canvas[py : py + nh, px : px + nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    return canvas, scale, px, py


def decode(output, scale, px, py, w, h, conf):
    """(1, 4 + classes, anchors) YOLOv8/11 output -> Detection list in [0, 1] coordinates."""
    pred = np.asarray(output, np.float32).reshape(output.shape[-2], output.shape[-1]).T
    scores = pred[:, 4:]
    cls = scores.argmax(1)
    best = scores[np.arange(len(cls)), cls]
    keep = best >= conf
    if not keep.any():
        return []
    boxes, cls, best = pred[keep, :4], cls[keep], best[keep]
    xywh = np.column_stack(
        [boxes[:, 0] - boxes[:, 2] / 2, boxes[:, 1] - boxes[:, 3] / 2, boxes[:, 2:4]]
    )
    picked = cv2.dnn.NMSBoxesBatched(
        xywh.tolist(), best.tolist(), cls.tolist(), conf, NMS_IOU, top_k=MAX_DET
    )
    out = []
    for i in np.asarray(picked, int).reshape(-1)[:MAX_DET]:
        x, y, bw, bh = xywh[i]
        x1, x2 = np.clip([(x - px) / scale / w, (x + bw - px) / scale / w], 0, 1)
        y1, y2 = np.clip([(y - py) / scale / h, (y + bh - py) / scale / h], 0, 1)
        name = NAMES[cls[i]] if cls[i] < len(NAMES) else str(int(cls[i]))
        out.append(Detection(name, float(best[i]), (float(x1), float(y1), float(x2), float(y2))))
    out.sort(key=lambda d: -d.conf)
    return out


class OnnxDetector:
    device = "opencv-cpu"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.model_path = model_path(self.settings)
        if not self.model_path.is_file():
            raise RuntimeError(
                f"No ONNX model at {self.model_path}. Put yolo11n.onnx in data/models/ "
                "(exported on a machine with PyTorch; see server/detect_onnx.py)."
            )
        self.net = cv2.dnn.readNetFromONNX(str(self.model_path))
        self.size = 640  # the export is fixed at 640 x 640, whatever YOLO_IMGSZ says
        self.detect(np.zeros((640, 480, 3), dtype=np.uint8))

    def detect(self, frame_rgb) -> list[Detection]:
        h, w = frame_rgb.shape[:2]
        image, scale, px, py = letterbox(np.ascontiguousarray(frame_rgb), self.size)
        # The image is already RGB, which is what the model was trained on: no swap.
        self.net.setInput(cv2.dnn.blobFromImage(image, 1 / 255.0, swapRB=False))
        return decode(self.net.forward(), scale, px, py, w, h, self.settings.yolo_conf)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", type=Path, help="a photo to run it on")
    args = parser.parse_args()
    detector = OnnxDetector(Settings(detector="onnx"))
    print(f"PASS ONNX YOLO loaded and warmed: {detector.model_path} ({detector.device})")
    if args.image:
        bgr = cv2.imread(str(args.image))
        if bgr is None:
            raise SystemExit(f"cannot read {args.image}")
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        start = time.monotonic()
        found = detector.detect(rgb)
        ms = (time.monotonic() - start) * 1000
        print(f"{len(found)} objects in {ms:.0f} ms:")
        for d in found:
            print(f"  {d.cls:<14} {d.conf:.2f}  box {tuple(round(v, 3) for v in d.bbox)}")


if __name__ == "__main__":
    main()
