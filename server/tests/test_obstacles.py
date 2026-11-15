"""Obstacles from detections: floor position, memory, and routes that bend around them."""

import math

import pytest

from server.obstacles import CLEARANCE_M, MIN_HITS, ObstacleMap, floor_point, to_map
from server.venue import Start, Venue

W, H, HFOV = 720, 960, 55.0
POS = {"x": 0.0, "y": 0.0, "z": 1.4}



def test_floor_point_straight_ahead_from_the_tilt():
    # looking 25 degrees down from 1.4 m, the image centre meets the floor at 1.4 / tan(25)
    f, r = floor_point([0.45, 0.3, 0.55, 0.5], W, H, -25.0, 0.0, HFOV)
    assert math.isclose(f, 1.4 / math.tan(math.radians(25)), rel_tol=1e-6) and abs(r) < 1e-9
    lower = floor_point([0.45, 0.4, 0.55, 0.7], W, H, -25.0, 0.0, HFOV)
    assert lower[0] < f  # lower in the image is nearer
    right = floor_point([0.8, 0.3, 0.9, 0.5], W, H, -25.0, 0.0, HFOV)
    assert right[1] > 0  # right of centre is to the right
    assert floor_point([0.4, 0.5, 0.6, 0.99], W, H, -25.0, 0.0, HFOV) is None  # cut off: too close
    assert floor_point([0.4, 0.0, 0.6, 0.2], W, H, 10.0, 0.0, HFOV) is None  # above the horizon


def test_to_map_uses_the_heading():
    x, y = to_map(2.0, 0.0, 1.0, 1.0, 90.0)  # facing east
    assert math.isclose(x, 3.0) and math.isclose(y, 1.0, abs_tol=1e-9)
    x, y = to_map(0.0, 1.0, 0.0, 0.0, 0.0)  # facing north, right is east
    assert math.isclose(x, 1.0) and math.isclose(y, 0.0, abs_tol=1e-9)


def chair(conf=0.8, bbox=(0.45, 0.3, 0.55, 0.5)):
    return {"cls": "chair", "conf": conf, "bbox": list(bbox)}


def test_an_obstacle_counts_after_two_sightings_and_merges():
    m = ObstacleMap()
    m.update(0.0, [chair()], (W, H), -25.0, 0.0, POS, 0.0, HFOV)
    assert m.active() == [] and len(m.items) == 1
    m.update(1.0, [chair(bbox=(0.46, 0.3, 0.56, 0.51))], (W, H), -25.0, 0.0, POS, 0.0, HFOV)
    assert len(m.active()) == 1 and m.active()[0].hits == MIN_HITS
    o = m.active()[0]
    assert abs(o.x) < 0.3 and 2.5 < o.y < 3.3  # about 3 m north


def test_ignored_classes_low_confidence_and_no_position():
    m = ObstacleMap()
    m.update(0.0, [{"cls": "tv", "conf": 0.9, "bbox": [0.4, 0.3, 0.6, 0.5]}, chair(conf=0.2)],
             (W, H), -25.0, 0.0, POS, 0.0, HFOV)
    m.update(0.5, [chair()], (W, H), -25.0, 0.0, None, 0.0, HFOV)
    assert m.items == []


def test_forgotten_when_in_view_and_not_seen():
    m = ObstacleMap()
    for t in (0.0, 1.0):
        m.update(t, [chair()], (W, H), -25.0, 0.0, POS, 0.0, HFOV)
    for t in (2.0, 3.0, 4.0):
        m.update(t, [], (W, H), -25.0, 0.0, POS, 0.0, HFOV)
    assert m.items == []


def test_kept_when_out_of_view_and_people_expire_fast():
    m = ObstacleMap()
    person = {"cls": "person", "conf": 0.9, "bbox": [0.45, 0.2, 0.55, 0.5]}
    for t in (0.0, 1.0):
        m.update(t, [chair(), person], (W, H), -25.0, 0.0, POS, 0.0, HFOV)
    for t in (2.0, 3.0, 4.0, 9.0):
        m.update(t, [], (W, H), -25.0, 0.0, POS, 180.0, HFOV)  # turned around: not in view
    assert [o.cls for o in m.items] == ["chair"]


@pytest.fixture()
def venue():
    return Venue.load(marked_path=None)


def test_route_bends_around_an_obstacle(venue):
    start = Start("1", (406, 290))  # west corridor at 102, going south past 101 to 198
    plain = venue.route("198", start)["legs"][0]["px"]
    assert plain
    rad = (0.4 + CLEARANCE_M) * venue.scale("1")
    venue.set_obstacles({"1": [(406.0, 420.0, rad)]})  # a chair in the corridor, before the atrium door
    bent = venue.route("198", start)
    assert bent["ok"]
    pts = bent["legs"][0]["px"]
    for a, b in zip(pts, pts[1:]):
        for t in (i / 10 for i in range(11)):
            u, v = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            assert math.dist((u, v), (406, 420)) > rad * 0.85, (a, b)
    venue.set_obstacles({})
    assert venue.route("198", start)["legs"][0]["px"] == plain  # cleared: the usual path again
