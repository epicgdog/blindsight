"""Dropped pin destinations (server/entrance_nav.py)."""

import numpy as np

from server.entrance_nav import EntranceGuide, EntranceMap, plan

# A corridor of scan points 3 m apart along x, entrance at the west end; one wall north of p3.
POINTS = {"p0": (0.0, 0.0), "p1": (3.0, 0.0), "p2": (6.0, 0.0), "p3": (9.0, 0.0)}
WALL = np.array([[7.0, 1.0, 11.0, 1.0]])


def make(walls=WALL):
    return EntranceMap(POINTS, {k: k for k in POINTS}, walls, {}, goal="p0")


def test_pin_with_clear_line_gets_a_last_leg_to_the_exact_point():
    m = make()
    out = m.set_destination((6.5, -1.0))
    assert out["ok"] and not out["moved"] and m.goal_kind == "pin"
    r = plan(m, POINTS["p0"], "p0")
    assert r.pts[-1] == (6.5, -1.0)


def test_pin_behind_a_wall_with_no_clear_scan_point_is_moved():
    m = make()
    out = m.set_destination((9.0, 30.0))  # far away: no scan point within reach
    assert out["ok"] and out["moved"]
    assert (out["x"], out["y"]) in [(round(x, 2), round(y, 2)) for x, y in POINTS.values()]


def test_pin_never_ends_through_a_wall():
    m = make()
    m.set_destination((9.0, 2.0))  # north of the wall above p3
    node = m.goal
    assert node != "p3" or m.goal_xy != (9.0, 2.0)


def test_clear_destination_restores_the_entrance_route():
    m = make()
    before = plan(m, POINTS["p3"], "p3").pts
    m.set_destination((6.5, -1.0))
    m.clear_destination()
    assert m.goal == "p0" and m.goal_kind == "entrance"
    assert plan(m, POINTS["p3"], "p3").pts == before


def test_guide_arrives_at_the_pin_with_its_label():
    m = make()
    m.set_destination((3.4, 0.0), "the kitchen")
    g = EntranceGuide(m)
    g.start("p1")
    text = g.update(0.0, "p1", "confident", None, {"x": 3.3, "y": 0.0, "sigma_m": 0.3})
    assert text == "You're at the kitchen."
    assert g.status == {"active": False, "arrived": True, "destination": "the kitchen"}


def test_replan_starts_over_toward_the_new_destination():
    m = make()
    g = EntranceGuide(m)
    g.start("p3")
    m.set_destination((6.5, -1.0))
    out = g.replan("p0", None)
    assert out["ok"] and g.route is not None and g.route.pts[-1] == (6.5, -1.0)
