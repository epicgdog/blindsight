"""heading_observation: the frame's own camera direction, handed to the heading filter."""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localize import heading_observation  # noqa: E402

BEST = SimpleNamespace(place="pano-1", yaw=123.456, inliers=40)


def fix(forward, inliers=25):
    """A PositionFix-like object whose camera-forward axis (column 2 of R_cw) is `forward`."""
    f = np.asarray(forward, float)
    right = np.cross([0.0, 1.0, 0.0], f)
    right = right / np.linalg.norm(right) if np.linalg.norm(right) > 1e-9 else np.array([1.0, 0, 0])
    down = np.cross(f, right)
    return SimpleNamespace(R_cw=np.column_stack([right, down, f]), inliers=inliers)


def test_no_verified_match_means_no_observation():
    assert heading_observation(False, BEST, None) is None
    assert heading_observation(True, None, None) is None
    assert heading_observation(True, SimpleNamespace(place="p", yaw=None, inliers=9), None) is None


def test_without_pnp_the_view_yaw_is_reported_alone():
    assert heading_observation(True, BEST, None) == {
        "pano": "pano-1", "yaw": 123.46, "inliers": 40, "pnp_yaw": None}


@pytest.mark.parametrize("forward, expected", [
    ((0, 0, 1), 0), ((1, 0, 0), 90), ((0, 0, -1), 180), ((-1, 0, 0), 270), ((1, 0, 1), 45),
])
def test_pnp_yaw_is_the_camera_forward_axis_in_the_pano_frame(forward, expected):
    out = heading_observation(True, BEST, fix(forward, inliers=31))
    assert out["pnp_yaw"] == pytest.approx(expected, abs=0.01)
    assert out["pnp_inliers"] == 31


def test_looking_straight_up_gives_no_pnp_yaw():
    out = heading_observation(True, BEST, fix((0, 1, 0)))
    assert out["pnp_yaw"] is None
    assert "pnp_inliers" not in out


def test_a_fix_without_a_rotation_is_ignored():
    out = heading_observation(True, BEST, SimpleNamespace(R_cw=None, inliers=5))
    assert out["pnp_yaw"] is None


def test_values_are_plain_json_types():
    import json

    out = heading_observation(True, SimpleNamespace(place="p", yaw=np.float64(10.0), inliers=np.int64(7)), fix((1, 0, 0)))
    json.dumps(out)  # numpy scalars would raise
