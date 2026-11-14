"""Part 3: temporal state machine (doc, 'Tests you own': state machine)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localize import LOCKED, LOST, UNCERTAIN, LocStateMachine  # noqa: E402


def sm(**kw):
    return LocStateMachine(agree_deg=15, jump_deg=40, lost_after=5, **kw)


def test_two_of_three_agreeing_gives_locked():
    m = sm()
    assert m.step(True, 100.0, None).state == UNCERTAIN
    r = m.step(True, 108.0, None)
    assert r.state == LOCKED
    assert abs(r.yaw - 108.0) < 1e-9


def test_two_matches_with_disagreeing_yaw_are_not_locked():
    m = sm()
    m.step(True, 100.0, None)
    assert m.step(True, 130.0, None).state == UNCERTAIN  # 30 deg apart > YAW_AGREE_DEG
    assert m.step(True, 133.0, None).state == LOCKED  # 130 and 133 agree


def test_two_of_three_with_a_failure_in_between():
    m = sm()
    m.step(True, 50.0, None)
    m.step(False, None, None)
    assert m.step(True, 55.0, None).state == LOCKED


def test_single_failure_does_not_give_lost():
    m = sm()
    m.step(True, 10.0, None)
    m.step(True, 12.0, None)
    r = m.step(False, None, None)
    assert r.state == LOCKED  # still 2 of the last 3
    assert r.state != LOST


def test_five_consecutive_failures_give_lost_and_four_do_not():
    m = sm()
    m.step(True, 10.0, None)
    m.step(True, 12.0, None)
    states = [m.step(False, None, None).state for _ in range(5)]
    assert states[:4] == [LOCKED, UNCERTAIN, UNCERTAIN, UNCERTAIN]
    assert states[4] == LOST
    r = m.step(False, None, None)
    assert r.state == LOST and r.yaw is None and r.conf == 0.0


def test_recovers_from_lost():
    m = sm()
    for _ in range(5):
        m.step(False, None, None)
    assert m.state == LOST
    assert m.step(True, 200.0, None).state == UNCERTAIN
    assert m.step(True, 202.0, None).state == LOCKED


def test_heading_compensates_yaw_agreement_while_turning():
    m = sm()
    # user turns 40 deg between frames; the compass sees the same turn
    m.step(True, 10.0, 300.0)
    assert m.step(True, 50.0, 340.0).state == LOCKED


def test_gyro_carries_yaw_between_fixes():
    m = sm()
    m.step(True, 10.0, 100.0)
    m.step(True, 12.0, 102.0)
    r = m.step(False, None, 132.0)  # turned 30 deg right, no visual fix
    assert abs(r.yaw - 42.0) < 1e-6
    assert 0 < r.conf < 1


def test_visual_jump_needs_two_frames():
    m = sm()
    m.step(True, 10.0, None)
    m.step(True, 12.0, None)
    r = m.step(True, 100.0, None)  # jumps 88 deg from the prediction
    assert r.pending_jump and not r.accepted
    assert abs(r.yaw - 12.0) < 1e-9  # the jump is ignored for now
    r = m.step(True, 103.0, None)  # a second frame confirms it
    assert r.accepted and abs(r.yaw - 103.0) < 1e-9
    assert r.state == LOCKED  # confirmed pair agrees


def test_unconfirmed_jump_is_dropped():
    m = sm()
    m.step(True, 10.0, None)
    m.step(True, 12.0, None)
    m.step(True, 100.0, None)  # outlier
    r = m.step(True, 14.0, None)  # back to normal
    assert r.accepted and abs(r.yaw - 14.0) < 1e-9 and r.state == LOCKED


def test_three_of_three_fallback():
    m = sm(need=3)
    m.step(True, 10.0, None)
    assert m.step(True, 11.0, None).state == UNCERTAIN
    assert m.step(True, 12.0, None).state == LOCKED
