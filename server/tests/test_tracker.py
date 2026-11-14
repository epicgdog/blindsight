"""Kalman position tracker (server/tracker.py)."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracker import GATE_CHI2, REINIT_N, Measurement, PositionTracker, fix_sigma  # noqa: E402


def m(x, y, z=1.4, s=0.2, place="a"):
    return Measurement(np.array([x, y, z], float), s, place)


def test_still_fixes_average_and_shrink_uncertainty():
    rng = np.random.default_rng(0)
    tr = PositionTracker()
    for k in range(30):
        tr.step(0.25 * k, False, m(2 + rng.normal(0, 0.2), 3 + rng.normal(0, 0.2)))
    assert np.linalg.norm(tr.xyz[:2] - [2, 3]) < 0.12
    assert tr.sigma_m < 0.06 and tr.n_accepted == 30


def test_far_jump_is_rejected_and_does_not_move_the_track():
    tr = PositionTracker()
    for k in range(5):
        tr.step(0.25 * k, True, m(0, 0))
    before = tr.xyz.copy()
    st = tr.step(1.5, True, m(15, 0, place="far"))
    assert st.rejected and st.d2 > GATE_CHI2
    assert np.allclose(tr.xyz, before, atol=0.05) and tr.place == "a"


def test_walking_follows_real_motion():
    tr = PositionTracker()
    for k in range(40):  # 1.2 m/s along x, fixes every 0.25 s
        st = tr.step(0.25 * k, True, m(1.2 * 0.25 * k, 0))
        assert not st.rejected, k
    assert abs(tr.xyz[0] - 1.2 * 0.25 * 39) < 0.3 and abs(tr.x[2] - 1.2) < 0.3


def test_consistent_rejected_fixes_restart_the_track_when_walking():
    tr = PositionTracker()
    for k in range(5):
        tr.step(0.25 * k, True, m(0, 0))
    out = [tr.step(1.5 + 0.25 * k, True, m(12, 1, place="b")) for k in range(REINIT_N)]
    assert all(o.rejected for o in out[:-1]) and out[-1].reinit
    assert tr.place == "b" and np.allclose(tr.xyz[:2], [12, 1])


def test_standing_still_a_repeated_lookalike_never_restarts():
    tr = PositionTracker()
    for k in range(5):
        tr.step(0.25 * k, False, m(0, 0))
    for k in range(10):
        st = tr.step(1.5 + 0.25 * k, False, m(12, 1, place="b"))
        assert st.rejected and not st.reinit
    assert tr.place == "a"


def test_scattered_rejected_fixes_do_not_restart():
    tr = PositionTracker()
    for k in range(5):
        tr.step(0.25 * k, True, m(0, 0))
    for k, (x, y) in enumerate([(12, 1), (-9, 4), (5, -11), (14, 8)]):
        assert not tr.step(1.5 + 0.25 * k, True, m(x, y, place="b")).reinit


def test_other_floor_is_rejected_even_when_close_in_plan():
    tr = PositionTracker()
    for k in range(5):
        tr.step(0.25 * k, True, m(0, 0, z=1.4))
    assert tr.step(1.5, True, m(0.2, 0, z=3.4)).rejected


def test_no_measurement_only_predicts():
    tr = PositionTracker()
    assert not tr.step(0.0, True, None).accepted and not tr.ready
    tr.step(0.0, True, m(1, 1))
    s0 = tr.sigma_m
    tr.step(1.0, True, None)
    assert tr.sigma_m > s0


def test_fix_sigma_prefers_more_inliers_and_smaller_offsets():
    assert fix_sigma(200, 0.2) < fix_sigma(30, 0.2) < fix_sigma(30, 3.0) <= 1.5
