"""Pure helpers behind the viewer's live pano ranking and footprint box."""

import math

import numpy as np
import pytest

from server.matchviz import footprint_polygon, rank_places


def test_rank_places_orders_by_best_view_and_dedupes_places():
    sims = np.array([0.2, 0.9, 0.5, 0.7, 0.1])
    places = ["A", "A", "B", "C", "C"]
    rows = rank_places(sims, places, top=8)
    assert [r["place"] for r in rows] == ["A", "C", "B"]
    assert [r["sim"] for r in rows] == [0.9, 0.7, 0.5]
    assert rows[0]["ref"] == 1  # the single best view of that place


def test_rank_places_limits_and_attaches_inliers():
    sims = np.array([0.3, 0.8, 0.6])
    rows = rank_places(sims, ["A", "B", "C"], top=2, inliers={"B": 41, "C": 3})
    assert len(rows) == 2
    assert rows[0] == {"place": "B", "sim": 0.8, "ref": 1, "inliers": 41}
    assert rows[1]["inliers"] == 3


def test_rank_places_handles_empty_input():
    assert rank_places(np.array([]), [], top=5) == []


def test_rank_places_rounds_and_is_json_safe():
    rows = rank_places(np.array([0.123456789]), ["A"], top=1)
    assert rows[0]["sim"] == 0.1235 and isinstance(rows[0]["sim"], float)


def _centre(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return sum(xs) / len(xs), sum(ys) / len(ys), max(xs) - min(xs)


def test_footprint_is_centred_on_yaw_with_hfov_width():
    poly = footprint_polygon(yaw=90, pitch=0, roll=0, hfov=55, aspect=16 / 9, samples=4)
    cx, cy, width = _centre(poly)
    assert cx == pytest.approx(0.25, abs=0.005)  # yaw 90 of 360
    assert cy == pytest.approx(0.5, abs=0.01)
    assert width == pytest.approx(55 / 360, abs=0.004)


def test_footprint_pitch_up_moves_box_up():
    level = _centre(footprint_polygon(0, 0, 0, 55, 16 / 9))[1]
    up = _centre(footprint_polygon(0, 30, 0, 55, 16 / 9))[1]
    assert up < level - 0.1  # equirect y grows downward


def test_footprint_wraps_continuously_across_the_seam():
    poly = footprint_polygon(yaw=0, pitch=0, roll=0, hfov=55, aspect=4 / 3)
    xs = [p[0] for p in poly]
    assert min(xs) < 0 < max(xs)  # unwrapped: the viewer draws copies at +/-1
    assert max(xs) - min(xs) < 0.2


def test_footprint_taller_frame_covers_more_pitch():
    short = footprint_polygon(0, 0, 0, 55, 4 / 3)
    tall = footprint_polygon(0, 0, 0, 55, 16 / 9)
    span = lambda p: max(q[1] for q in p) - min(q[1] for q in p)  # noqa: E731
    assert span(tall) > span(short)


def test_footprint_points_are_finite_and_json_safe():
    poly = footprint_polygon(250, -10, 3, 55, 16 / 9, samples=5)
    assert len(poly) == 4 * 5
    assert all(len(p) == 2 and all(math.isfinite(v) for v in p) for p in poly)


def test_footprint_polar_view_stays_finite():
    poly = footprint_polygon(0, 89, 0, 55, 16 / 9)
    assert all(math.isfinite(v) for p in poly for v in p)


def test_rank_places_puts_verified_matches_before_retrieval_only():
    sims = np.array([0.9, 0.6, 0.8])
    rows = rank_places(sims, ["A", "B", "C"], top=3, inliers={"B": 30, "C": 5})
    assert [r["place"] for r in rows] == ["B", "C", "A"]  # inliers first, then similarity


def test_rank_places_orders_unverified_places_by_similarity():
    sims = np.array([0.5, 0.9, 0.7])
    rows = rank_places(sims, ["A", "B", "C"], top=3)
    assert [r["place"] for r in rows] == ["B", "C", "A"]
