"""Part 3: the synthetic feed hooks used by Part 1's sim client and Part 2's video renderer."""

import importlib.util
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from p3_room import Room, write_dataset  # noqa: E402


def load_like_part2():
    """Part 2's renderer loads the file without registering it in sys.modules."""
    spec = importlib.util.spec_from_file_location("synthetic_feed_isolated", ROOT / "tools" / "synthetic_feed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_frames_hook_yields_jpegs_of_the_pano(tmp_path):
    data = write_dataset(tmp_path, Room(), S=128, ids=["room_p0", "room_p1"])
    mod = load_like_part2()
    out = list(mod.frames(4, size=(120, 160), data_dir=data))
    assert len(out) == 4
    for jpeg, w, h in out:
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        assert (w, h) == (120, 160) and img.shape[:2] == (160, 120)
    assert not np.array_equal(cv2.imdecode(np.frombuffer(out[0][0], np.uint8), 0),
                              cv2.imdecode(np.frombuffer(out[2][0], np.uint8), 0))  # the camera pans


def test_frames_hook_fails_fast_without_pano_data(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_like_part2().frames(3, data_dir=tmp_path)


def test_iter_feed_gives_headings(tmp_path):
    data = write_dataset(tmp_path, Room(), S=128, ids=["room_p0", "room_p1"])
    mod = load_like_part2()
    fr = list(mod.iter_feed(data_dir=data, fps=10, seconds=0.3, size=(60, 80)))
    assert len(fr) == 3 and all(f.heading_deg is not None and f.frame_rgb.shape == (80, 60, 3) for f in fr)
