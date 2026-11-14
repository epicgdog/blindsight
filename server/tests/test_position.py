"""Depth + PnP position (server/position.py) on the procedural room."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from geometry import SiftMatcher, cam_to_world, extract_features, intrinsics, normalize_contrast, verify  # noqa: E402
from p3_room import HI, LO, Room, pos3  # noqa: E402
from position import (  # noqa: E402
    BoxDepth,
    PlaceFrame,
    frame_candidates,
    frame_matrix,
    position_from_match,
    quat_to_matrix,
    solve_pnp,
)
from refs import CubeSampler  # noqa: E402

W, H, HFOV = 360, 480, 55.0
P0 = pos3("room_p0")


@pytest.fixture(scope="module")
def room():
    return Room()


@pytest.fixture(scope="module")
def sampler(room):
    return CubeSampler(room.faces(P0, 768))


@pytest.fixture(scope="module")
def matcher():
    return SiftMatcher(nfeatures=3000)


def test_solve_pnp_exact_points():
    rng = np.random.default_rng(0)
    K = intrinsics(W, H, HFOV)
    C = cam_to_world(30, -5, 2)
    centre = np.array([0.7, -0.1, 0.4])
    X = centre + (rng.uniform([-2, -2, 3], [2, 2, 6], (80, 3)) @ C.T)
    cam = (X - centre) @ C
    x = (cam[:, :2] / cam[:, 2:]) * K[0, 0] + K[:2, 2]
    fix = solve_pnp(X, x, K)
    assert fix is not None and np.linalg.norm(fix.local - centre) < 1e-4
    assert np.allclose(fix.R_cw, C, atol=1e-5)


def test_box_depth_hits_walls():
    d = BoxDepth(LO, HI)
    pts, ok = d.cast(P0, np.array([[1.0, 0, 0], [0, -1.0, 0], [0, 0, 1.0]]))
    assert ok.all()
    assert np.allclose(pts, [[HI[0], P0[1], P0[2]], [P0[0], LO[1], P0[2]], [P0[0], P0[1], HI[2]]])


def test_place_frame_round_trip():
    M = frame_matrix({"swap": "b", "base_deg": 90, "quat": "q"}, {"x": 0.0, "y": 0.0, "z": 0.2, "w": 0.98})
    assert np.allclose(M @ M.T, np.eye(3)) and np.isclose(np.linalg.det(M), 1.0)
    f = PlaceFrame(np.array([1.0, 2.0, 1.4]), M)
    p = np.array([[0.3, -0.2, 2.0]])
    assert np.allclose(f.to_local(f.to_depth(p)), p)
    assert len(frame_candidates()) == 24
    assert np.allclose(quat_to_matrix({"x": 0, "y": 0, "z": 0, "w": 1}), np.eye(3))


@pytest.mark.parametrize("offset,yaw,pitch", [
    ((0.0, 0.0, 0.0), 40.0, 0.0),
    ((0.8, 0.0, 0.3), 100.0, -4.0),
    ((-0.6, 0.0, -0.9), 220.0, 3.0),
    ((0.4, -0.2, 1.2), 300.0, 0.0),
])
def test_position_from_offset_view(room, sampler, matcher, offset, yaw, pitch):
    """Live view rendered off the scan point; PnP against the pano's reference view finds it."""
    K = intrinsics(W, H, HFOV)
    offset = np.asarray(offset)
    live_img = room.render(P0 + offset, yaw, pitch, 0.0, (W, H), HFOV)
    ref_yaw = round(yaw / 15) * 15.0
    ref_img = sampler.render(ref_yaw, 0.0, 0.0, (W, H), HFOV)
    live = extract_features(matcher, normalize_contrast(live_img))
    ref = extract_features(matcher, normalize_contrast(ref_img))
    v = verify(matcher, live, ref, K, K)
    fix = position_from_match(v, ref_yaw, 0.0, 0.0, K, K, BoxDepth(LO, HI), PlaceFrame(P0))
    assert fix is not None
    err = float(np.linalg.norm(fix.local - offset))
    assert err < 0.25, (fix.to_dict(), err)
    assert np.allclose(fix.world, P0 + fix.local)
