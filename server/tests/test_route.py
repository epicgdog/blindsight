"""Route geometry (maneuvers, projection) and Progress (distance along the route, off-route)."""

import pytest

from server.route import (
    BACK_M,
    CAL_MIN_STEPS,
    OFF_ROUTE_S,
    STRIDE_M,
    Progress,
    Route,
    bearing_deg,
    clock,
    turn_between,
)


# ---- angles ---------------------------------------------------------------------------
@pytest.mark.parametrize("to, expected", [((0, 1), 0), ((1, 0), 90), ((0, -1), 180), ((-1, 0), 270)])
def test_bearing_is_clockwise_from_plus_y(to, expected):
    assert bearing_deg((0, 0), to) == pytest.approx(expected)


def test_turn_between_is_signed_and_takes_the_short_way_round():
    assert turn_between(350, 10) == pytest.approx(20)  # across north: a small right turn
    assert turn_between(10, 350) == pytest.approx(-20)
    assert turn_between(0, 90) == pytest.approx(90)
    assert abs(turn_between(0, 180)) == pytest.approx(180)


@pytest.mark.parametrize("turn, expected", [(0, 12), (14, 12), (30, 1), (90, 3), (-90, 9), (-30, 11), (180, 6), (-180, 6)])
def test_clock_is_twelve_straight_ahead_and_three_on_the_right(turn, expected):
    assert clock(turn) == expected


# ---- Route ----------------------------------------------------------------------------
L_RIGHT = [(0, 0), (0, 5), (5, 5)]  # north 5 m, then east 5 m


def test_straight_route_has_only_the_arrival():
    r = Route([(0, 0), (0, 4), (0, 10)])
    assert r.length == pytest.approx(10)
    assert r.turns == []
    assert [(m.say, m.s, m.turn_deg) for m in r.maneuvers] == [("arrive", pytest.approx(10), 0.0)]


def test_repeated_points_are_dropped():
    r = Route([(0, 0), (0, 0.01), (0, 5)])
    assert len(r.pts) == 2
    assert r.length == pytest.approx(5)


def test_right_and_left_turns_are_named_and_placed():
    right = Route(L_RIGHT)
    assert [(m.say, m.s, m.seg) for m in right.turns] == [("turn right", pytest.approx(5), 1)]
    assert right.turns[0].turn_deg == pytest.approx(90)
    left = Route([(0, 0), (0, 5), (-5, 5)])
    assert left.turns[0].say == "turn left"
    assert left.turns[0].turn_deg == pytest.approx(-90)
    assert left.maneuvers[-1].say == "arrive"


def test_a_sharp_reversal_is_turn_around():
    assert Route([(0, 0), (0, 5), (0, 1)]).turns[0].say == "turn around"


def test_small_bends_and_short_jogs_are_not_maneuvers():
    assert Route([(0, 0), (0, 5), (1, 10)]).turns == []  # about 11 degrees
    assert Route([(0, 0), (0, 0.5), (5, 0.5)]).turns == []  # the first leg is shorter than MIN_LEG_M


def test_segment_at_clamps_to_the_route():
    r = Route(L_RIGHT)
    assert [r.segment_at(s) for s in (-3, 2, 5, 7, 100)] == [0, 0, 1, 1, 1]


def test_project_gives_distance_along_cross_track_and_leg():
    r = Route(L_RIGHT)
    s, d, i = r.project((1, 2))
    assert (s, d, i) == (pytest.approx(2), pytest.approx(1), 0)
    s, d, i = r.project((3, 6))
    assert (s, d, i) == (pytest.approx(8), pytest.approx(1), 1)
    assert r.project((0, -3))[:2] == (pytest.approx(0), pytest.approx(3))  # before the start


def test_a_single_point_route_does_not_crash():
    r = Route([(0, 0)])
    assert r.length == 0
    assert r.project((3, 4)) == (0.0, pytest.approx(5), 0)
    assert [m.say for m in r.maneuvers] == ["arrive"]


# ---- Progress -------------------------------------------------------------------------
STRAIGHT = [(0, 0), (0, 30)]


def test_a_fix_moves_progress_forward():
    p = Progress(Route(STRAIGHT))
    p.update(0, (0, 3), None, None)
    assert p.s == pytest.approx(3)
    p.update(1, (0, 9), None, None)
    assert p.s == pytest.approx(9)


def test_steps_advance_progress_between_fixes_and_stop_at_the_end():
    p = Progress(Route([(0, 0), (0, 10)]))
    p.update(0, None, None, 10, fix=False)  # first step count is only remembered
    assert p.s == 0
    p.update(1, None, None, 20, fix=False)
    assert p.s == pytest.approx(10 * STRIDE_M)
    p.update(2, None, None, 200, fix=False)
    assert p.s == pytest.approx(10)  # never past the arrival


def test_fix_false_does_not_reapply_a_stale_position():
    p = Progress(Route(STRAIGHT))
    p.update(0, (0, 8), None, 0)
    p.update(1, (0, 1), None, 0, fix=False)  # an IMU sample: xy is stale and must be ignored
    assert p.s == pytest.approx(8)


def test_a_backtrack_needs_three_fixes_in_a_row():
    p = Progress(Route(STRAIGHT))
    p.update(0, (0, 8), None, None)
    back = (0, 8 - BACK_M - 3)
    p.update(1, back, None, None)
    p.update(2, back, None, None)
    assert p.s == pytest.approx(8)  # two bad fixes do not undo progress
    p.update(3, back, None, None)
    assert p.s == pytest.approx(back[1])


def test_a_good_fix_resets_the_backtrack_count():
    p = Progress(Route(STRAIGHT))
    p.update(0, (0, 8), None, None)
    p.update(1, (0, 2), None, None)
    p.update(2, (0, 2), None, None)
    p.update(3, (0, 8.5), None, None)  # forward again: the count starts over
    p.update(4, (0, 2), None, None)
    p.update(5, (0, 2), None, None)
    assert p.s == pytest.approx(8.5)


def test_a_small_step_back_is_ignored():
    p = Progress(Route(STRAIGHT))
    p.update(0, (0, 8), None, None)
    for t in range(1, 6):
        p.update(t, (0, 8 - BACK_M / 2), None, None)
    assert p.s == pytest.approx(8)


def test_off_route_needs_to_be_held_and_clears_when_back_on_the_route():
    p = Progress(Route(STRAIGHT))
    p.update(0, (5, 5), None, None)
    assert not p.off_route()
    p.update(OFF_ROUTE_S - 0.1, (5, 5), None, None)
    assert not p.off_route()
    p.update(OFF_ROUTE_S, (5, 5), None, None)
    assert p.off_route()
    p.update(OFF_ROUTE_S + 1, (0, 6), None, None)
    assert not p.off_route()


def test_losing_the_position_clears_off_route():
    p = Progress(Route(STRAIGHT))
    p.update(0, (5, 5), None, None)
    p.update(OFF_ROUTE_S, (5, 5), None, None)
    assert p.off_route()
    p.update(OFF_ROUTE_S + 1, None, None, None)
    assert not p.off_route()


def test_stride_is_learned_from_two_sure_fixes_far_apart():
    p = Progress(Route(STRAIGHT))
    p.update(0, (0, 2), 0.5, 0)
    p.update(1, (0, 12), 0.5, 20)  # 10 m in 20 steps
    assert p.stride == pytest.approx(0.5)


def test_stride_is_clamped_to_a_human_range():
    p = Progress(Route(STRAIGHT))
    p.update(0, (0, 2), 0.5, 0)
    p.update(1, (0, 12), 0.5, CAL_MIN_STEPS)  # 2.5 m per step is not a stride
    assert p.stride == pytest.approx(1.0)


def test_unsure_fixes_do_not_calibrate_the_stride():
    p = Progress(Route(STRAIGHT))
    p.update(0, (0, 2), 3.0, 0)
    p.update(1, (0, 12), 3.0, 20)
    assert p.stride == pytest.approx(STRIDE_M)
