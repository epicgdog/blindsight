"""Part 3: geometry (doc, 'Tests you own': geometry) and masking."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from geometry import (  # noqa: E402
    SiftMatcher,
    ang_diff,
    blank_boxes,
    cam_to_world,
    estimate_hfov,
    extract_features,
    intrinsics,
    keep_outside_boxes,
    live_pose,
    movable_boxes_px,
    normalize_contrast,
    verify,
    ypr_from_cam_to_world,
)
from p3_room import Room, _texture, pos3  # noqa: E402
from refs import CubeSampler  # noqa: E402

N_MIN = 30
W, H, HFOV = 360, 480, 55.0


@pytest.fixture(scope="module")
def room():
    return Room()


@pytest.fixture(scope="module")
def sampler(room):
    return CubeSampler(room.faces(pos3("room_p0"), 512))


@pytest.fixture(scope="module")
def matcher():
    return SiftMatcher()


def test_camera_round_trip():
    for y, p, r in [(0, 0, 0), (37, -8, 3), (275, 12, -4), (359, 0, 1)]:
        yy, pp, rr = ypr_from_cam_to_world(cam_to_world(y, p, r))
        assert ang_diff(yy, y) < 1e-6 and abs(pp - p) < 1e-6 and abs(rr - r) < 1e-6


@pytest.mark.parametrize("dyaw,pitch,roll", [(7, 0, 0), (-12, 4, 2), (20, -6, -3), (-25, 8, 0)])
def test_rotating_a_reference_view_returns_that_yaw(sampler, matcher, dyaw, pitch, roll):
    K = intrinsics(W, H, HFOV)
    ref_yaw = 135.0
    ref = extract_features(matcher, normalize_contrast(sampler.render(ref_yaw, 0, 0, (W, H), HFOV)))
    live = extract_features(matcher, normalize_contrast(sampler.render(ref_yaw + dyaw, pitch, roll, (W, H), HFOV)))
    v = verify(matcher, live, ref, K, K)
    assert v.inliers >= N_MIN and v.grid_cells >= 3
    yaw, p, r = live_pose(v, ref_yaw, 0.0)
    assert ang_diff(yaw, ref_yaw + dyaw) < 3.0
    assert abs(p - pitch) < 3.0


def test_unrelated_image_gives_fewer_than_n_min_inliers(sampler, matcher):
    K = intrinsics(W, H, HFOV)
    ref = extract_features(matcher, normalize_contrast(sampler.render(0, 0, 0, (W, H), HFOV)))
    unrelated = _texture(W, H, seed=12345, kind="wall")
    v = verify(matcher, extract_features(matcher, normalize_contrast(unrelated)), ref, K, K)
    assert v.inliers < N_MIN


def test_other_scan_point_loses_rotation_inliers(room, sampler, matcher):
    """A view of the same walls from 3.5 m away keeps homography inliers but fewer
    rotation-consistent ones than the true scan point."""
    K = intrinsics(W, H, HFOV)
    ref = extract_features(matcher, normalize_contrast(sampler.render(90, 0, 0, (W, H), HFOV)))
    here = verify(matcher, extract_features(matcher, normalize_contrast(room.render(pos3("room_p0"), 98))), ref, K, K)
    away = verify(matcher, extract_features(matcher, normalize_contrast(room.render(pos3("room_p3"), 105))), ref, K, K)
    assert here.inliers > away.inliers
    assert away.inliers <= away.h_inliers


def test_hfov_estimate_recovers_true_fov(room, sampler, matcher):
    true_hfov = 62.0
    K_ref = intrinsics(W, H, HFOV)
    pairs = []
    for i, yaw in enumerate((20, 140, 250)):
        ref = extract_features(matcher, normalize_contrast(sampler.render(yaw, 0, 0, (W, H), HFOV)))
        img = room.render(pos3("room_p0"), yaw + 6, 2, 0, (W, H), true_hfov)
        live = extract_features(matcher, normalize_contrast(img))
        v = verify(matcher, live, ref, intrinsics(W, H, true_hfov), K_ref)
        pairs.append((v.live_pts, v.ref_pts, K_ref))
    hfov, _ = estimate_hfov(pairs, (W, H), 45, 75, 0.5)
    assert abs(hfov - true_hfov) <= 1.0


# ---------------------------------------------------------------- masking
def dets(*items):
    return [{"cls": c, "conf": 0.9, "bbox": b} for c, b in items]


def test_keypoints_inside_person_and_chair_boxes_are_discarded(sampler, matcher):
    img = normalize_contrast(sampler.render(45, 0, 0, (W, H), HFOV))
    d = dets(("person", (0.10, 0.20, 0.45, 0.90)), ("chair", (0.55, 0.50, 0.95, 0.95)))
    boxes = movable_boxes_px(d, W, H)
    assert len(boxes) == 2
    full = extract_features(matcher, img)
    masked = extract_features(matcher, img, boxes)
    assert (~keep_outside_boxes(full.kpts, boxes)).sum() > 20  # there was something to remove
    assert keep_outside_boxes(masked.kpts, boxes).all()  # nothing left inside the boxes
    assert len(masked) > 0


def test_static_classes_are_not_masked():
    d = dets(("door", (0.1, 0.1, 0.5, 0.5)), ("refrigerator", (0.5, 0.5, 0.9, 0.9)), ("person", (0, 0, 0.1, 0.1)))
    assert len(movable_boxes_px(d, W, H)) == 1


def test_detection_objects_with_attributes_are_accepted():
    class Det:
        cls, conf, bbox = "suitcase", 0.8, (0.2, 0.2, 0.4, 0.6)

    assert movable_boxes_px([Det()], W, H) == [(68, 91, 148, 293)]


def test_blank_boxes_removes_content_for_embedding(sampler):
    img = sampler.render(45, 0, 0, (W, H), HFOV)
    boxes = movable_boxes_px(dets(("backpack", (0.2, 0.2, 0.6, 0.6))), W, H)
    out = blank_boxes(img, boxes)
    x1, y1, x2, y2 = boxes[0]
    patch = out[y1:y2, x1:x2].reshape(-1, 3)
    assert (patch == patch[0]).all()
    assert (out[:y1] == img[:y1]).all()


def test_masking_keeps_localization_working_with_a_crowd(sampler, matcher):
    """A 'person' box over 40% of the frame still leaves enough verified evidence."""
    K = intrinsics(W, H, HFOV)
    ref = extract_features(matcher, normalize_contrast(sampler.render(200, 0, 0, (W, H), HFOV)))
    live_img = normalize_contrast(sampler.render(205, 0, 0, (W, H), HFOV))
    boxes = movable_boxes_px(dets(("person", (0.3, 0.0, 0.7, 1.0))), W, H)
    v = verify(matcher, extract_features(matcher, live_img, boxes), ref, K, K)
    assert v.inliers >= N_MIN
    assert np.all(keep_outside_boxes(v.live_pts, boxes))
