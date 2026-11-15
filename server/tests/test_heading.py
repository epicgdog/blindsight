"""HeadingKalman: heading in the mesh frame from compass, visual (view / PnP) and course fixes."""

import numpy as np
import pytest

from server.heading import MAX_SIGMA_DEG, REINIT_N, HeadingKalman, wrap180

# Pano-local forward (sin yaw, 0, cos yaw) -> mesh (sin yaw, cos yaw, 0): mesh heading == pano yaw.
FRAMES = {"p": np.array([[1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]])}


def obs(yaw, pnp=None, pano="p"):
    d = {"pano": pano, "yaw": yaw}
    if pnp is not None:
        d["pnp_yaw"] = pnp
    return d


def circ_diff(a, b):
    return abs(wrap180(a - b))


@pytest.mark.parametrize("d, expected", [(0, 0), (190, -170), (-190, 170), (360, 0), (180, -180)])
def test_wrap180(d, expected):
    assert wrap180(d) == pytest.approx(expected)


def test_no_heading_before_the_first_visual_fix():
    k = HeadingKalman(FRAMES)
    est = k.update_sensors(0.0, 90.0)
    assert est["heading_deg"] is None
    assert k.update_course(1.0, 45.0)["heading_deg"] is None  # a course alone never starts the filter


def test_first_view_fix_sets_the_heading():
    k = HeadingKalman(FRAMES)
    est = k.update_visual(0.0, obs(100))
    assert est["heading_deg"] == pytest.approx(100, abs=0.1)
    assert est["source"] == "view"


def test_pnp_is_used_when_it_agrees_with_the_view_and_ignored_when_it_does_not():
    agree = HeadingKalman(FRAMES).update_visual(0.0, obs(100, pnp=110))
    assert agree["source"] == "pnp"
    assert agree["heading_deg"] == pytest.approx(110, abs=0.1)
    disagree = HeadingKalman(FRAMES).update_visual(0.0, obs(100, pnp=200))
    assert disagree["source"] == "view"
    assert disagree["heading_deg"] == pytest.approx(100, abs=0.1)


@pytest.mark.parametrize("bad", [None, {}, {"pano": "p", "yaw": None}, {"pano": "unknown", "yaw": 90}])
def test_unusable_visual_observations_are_ignored(bad):
    k = HeadingKalman(FRAMES)
    assert k.update_visual(0.0, bad)["heading_deg"] is None


def test_a_late_frame_is_shifted_by_the_compass_change_since_capture():
    est = HeadingKalman(FRAMES).update_visual(0.0, obs(100), shift_deg=10)
    assert est["heading_deg"] == pytest.approx(110, abs=0.1)


def test_a_visual_fix_with_a_compass_teaches_the_bias():
    est = HeadingKalman(FRAMES).update_visual(0.0, obs(100), compass_deg=40)
    assert est["compass_bias_deg"] == pytest.approx(60, abs=0.1)  # mesh heading minus compass


def test_compass_turns_move_the_heading_between_visual_fixes():
    k = HeadingKalman(FRAMES)
    k.update_visual(0.0, obs(100), compass_deg=40)
    est = k.update_sensors(1.0, 70.0, moving=False)  # the phone turned 30 degrees
    assert 115 < est["heading_deg"] < 130
    assert est["source"] == "compass"


def test_the_first_compass_after_a_compassless_start_learns_the_bias():
    k = HeadingKalman(FRAMES)
    k.update_visual(0.0, obs(100))
    assert k.estimate["compass_bias_deg"] is None
    est = k.update_sensors(0.1, 40.0, moving=False)  # IMU samples come about every 0.1 s
    assert est["compass_bias_deg"] == pytest.approx(60, abs=0.1)
    assert est["heading_deg"] == pytest.approx(100, abs=1)


def test_a_nearby_visual_fix_is_fused():
    k = HeadingKalman(FRAMES)
    k.update_visual(0.0, obs(100, pnp=100))
    est = k.update_visual(0.0, obs(110, pnp=110))
    assert 100 < est["heading_deg"] < 110


def test_fusion_works_across_north():
    k = HeadingKalman(FRAMES)
    k.update_visual(0.0, obs(355, pnp=355))
    est = k.update_visual(0.0, obs(5, pnp=5))
    assert circ_diff(est["heading_deg"], 0) < 10


def test_a_wild_fix_is_rejected_until_several_agree_and_then_the_filter_restarts():
    k = HeadingKalman(FRAMES)
    k.update_visual(0.0, obs(100, pnp=100))
    for _ in range(REINIT_N - 1):
        est = k.update_visual(0.0, obs(280, pnp=280))
        assert circ_diff(est["heading_deg"], 100) < 1  # gated out
    est = k.update_visual(0.0, obs(280, pnp=280))
    assert circ_diff(est["heading_deg"], 280) < 1  # REINIT_N agreeing rejects restart it there


def test_rejects_that_disagree_do_not_restart_the_filter():
    k = HeadingKalman(FRAMES)
    k.update_visual(0.0, obs(100, pnp=100))
    for yaw in (200, 280, 20):
        k.update_visual(0.0, obs(yaw, pnp=yaw))
    assert circ_diff(k.estimate["heading_deg"], 100) < 1


def test_course_refines_a_running_filter_but_never_restarts_it():
    k = HeadingKalman(FRAMES)
    k.update_visual(0.0, obs(100, pnp=100))
    for _ in range(REINIT_N + 2):
        est = k.update_course(0.0, 280.0)  # a bad course, repeated
    assert circ_diff(est["heading_deg"], 100) < 1
    est = k.update_course(0.0, 110.0)
    assert 100 < est["heading_deg"] < 110


def test_uncertainty_grows_while_nothing_confirms_the_heading_and_drops_the_estimate():
    k = HeadingKalman(FRAMES)
    k.update_visual(0.0, obs(100))
    first = k.estimate["sigma_deg"]
    est = k.update_sensors(5.0, None, moving=True)
    assert est["sigma_deg"] > first
    est = k.update_sensors(10.0, None, moving=True)
    est = k.update_sensors(15.0, None, moving=True)
    assert est["sigma_deg"] > MAX_SIGMA_DEG
    assert est["heading_deg"] is None  # too unsure to report


def test_reset_forgets_everything():
    k = HeadingKalman(FRAMES)
    k.update_visual(0.0, obs(100), compass_deg=40)
    k.reset()
    assert k.estimate == {"heading_deg": None, "source": None, "sigma_deg": None}
    assert k.update_sensors(0.0, 10.0)["heading_deg"] is None


def test_update_runs_sensors_then_the_frames_visual_fix():
    est = HeadingKalman(FRAMES).update(0.0, obs(100), 40.0)
    assert est["heading_deg"] == pytest.approx(100, abs=0.1)
    assert est["compass_bias_deg"] == pytest.approx(60, abs=0.1)
