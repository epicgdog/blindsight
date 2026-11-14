"""Masking people and furniture out of the pano reference views."""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from refmask import compute_masks, mask_key, region_polygons  # noqa: E402


def view(name, w=120, h=160):
    return SimpleNamespace(name=name, image=np.zeros((h, w, 3), np.uint8))


def det(cls, box):
    return SimpleNamespace(cls=cls, conf=0.9, bbox=box)


def test_masks_cover_movable_classes_in_pixels(tmp_path):
    views = [view("a")]
    out = compute_masks(tmp_path, views, lambda img: [det("person", (0.25, 0.25, 0.75, 0.75))], "k")
    ((x1, y1, x2, y2),) = out["a"]
    assert 28 <= x1 <= 31 and 38 <= y1 <= 42  # 0.25 of 120x160, padded a little
    assert 88 <= x2 <= 92 and 118 <= y2 <= 122


def test_structure_is_not_masked(tmp_path):
    out = compute_masks(
        tmp_path, [view("a")], lambda img: [det("clock", (0.1, 0.1, 0.3, 0.3))], "k"
    )
    assert out["a"] == []


def test_views_without_detections_have_empty_masks(tmp_path):
    out = compute_masks(tmp_path, [view("a"), view("b")], lambda img: [], "k")
    assert out == {"a": [], "b": []}


def test_masks_are_cached_and_reused(tmp_path):
    calls = []

    def detect(img):
        calls.append(1)
        return [det("person", (0.2, 0.2, 0.4, 0.4))]

    views = [view("a"), view("b")]
    first = compute_masks(tmp_path, views, detect, "k")
    assert len(calls) == 2
    second = compute_masks(tmp_path, views, detect, "k")
    assert len(calls) == 2  # served from the cache
    assert second == first


def test_cache_is_invalidated_by_key_or_views(tmp_path):
    calls = []

    def detect(img):
        calls.append(1)
        return []

    compute_masks(tmp_path, [view("a")], detect, "k1")
    compute_masks(tmp_path, [view("a")], detect, "k2")  # different detector/threshold
    compute_masks(tmp_path, [view("a"), view("b")], detect, "k2")  # different view set
    assert len(calls) == 1 + 1 + 2


def test_detector_failure_degrades_to_no_masks(tmp_path):
    def boom(img):
        raise RuntimeError("model unavailable")

    out = compute_masks(tmp_path, [view("a")], boom, "k")
    assert out == {"a": []}


def test_mask_key_changes_with_inputs():
    assert mask_key("yolo11n", 0.35) != mask_key("yolo11n", 0.5)
    assert mask_key("yolo11n", 0.35) != mask_key("yolov8n", 0.35)
    assert mask_key("yolo11n", 0.35) == mask_key("yolo11n", 0.35)


def test_region_polygons_land_on_the_view_direction():
    views = [SimpleNamespace(name="a", yaw=90.0, pitch=0.0, roll=0.0, hfov=55.0)]
    masks = {"a": [(150, 200, 250, 400)]}  # centre box of a 400x600 view
    polys = region_polygons(views, masks, size=(400, 600))
    assert len(polys) == 1
    cx = sum(p[0] for p in polys[0]) / len(polys[0])
    cy = sum(p[1] for p in polys[0]) / len(polys[0])
    assert cx == pytest.approx(0.25, abs=0.01)  # yaw 90 of 360
    assert cy == pytest.approx(0.5, abs=0.03)


def test_region_polygons_skip_views_without_masks():
    views = [SimpleNamespace(name="a", yaw=0.0, pitch=0.0, roll=0.0, hfov=55.0)]
    assert region_polygons(views, {"a": []}, size=(400, 600)) == []
