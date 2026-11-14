"""Export cached YOLO weights into the native app's optional CoreML bundle.

Run with uv run --with coremltools==9.0 --with numpy==2.3.5
python tools/export_mobile_detector.py WEIGHTS.
Generated binary assets are intentionally excluded from git. Rebuild the native app
after export. Actual-device precision and latency validation remain required.
"""
import argparse
import shutil
import subprocess
from pathlib import Path

from ultralytics import YOLO


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("weights", type=Path)
    args = parser.parse_args()
    if not args.weights.is_file():
        parser.error("Supply an existing cached YOLO weights file")
    exported = Path(YOLO(str(args.weights)).export(format="coreml", imgsz=320, nms=True))
    out = Path(__file__).resolve().parents[1] / "app/modules/blindsight-tracking/ios/Models"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(["xcrun", "coremlcompiler", "compile", str(exported), str(out)], check=True)
    compiled = out / (exported.stem + ".mlmodelc")
    destination = out / "ObstacleDetector.mlmodelc"
    if destination.exists():
        raise FileExistsError("Existing detector preserved; move it aside before rebuilding")
    shutil.move(str(compiled), str(destination))
    print("CoreML detector exported. Rebuild and validate the native app on your iPhone.")


if __name__ == "__main__":
    main()
