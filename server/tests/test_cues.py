import pytest

from server.cues import describe, direction, distance
from server.protocol import Detection


def box(cls="chair", area=0.15, cx=0.5):
    # Full-height boxes make area precise and let tests independently set direction.
    return Detection(cls, 0.9, (cx - area / 2, 0, cx + area / 2, 1))


@pytest.mark.parametrize(
    "angle,expected",
    [
        (0, "ahead"),
        (-27, "left"),
        (27, "right"),
        (-8, "ahead"),
        (8, "ahead"),
        (-8.01, "slightly left"),
        (8.01, "slightly right"),
        (-18, "slightly left"),
        (18, "slightly right"),
        (-18.01, "left"),
        (18.01, "right"),
    ],
)
def test_direction_boundaries(angle, expected):
    assert direction(0.5 + angle / 55) == expected


@pytest.mark.parametrize(
    "area,expected",
    [
        (0.0299, None),
        (0.03, ""),
        (0.0999, ""),
        (0.10, "close"),
        (0.2999, "close"),
        (0.30, "very close"),
    ],
)
def test_distance_boundaries(area, expected):
    assert distance(area, "chair") == expected
    assert distance(0.001, "person") == ""


def test_describe_reports_direction_and_distance():
    d = describe(box("chair", area=0.35, cx=0.5))
    assert d["direction"] == "ahead" and d["distance"] == "very close" and d["distance_m"] is None
