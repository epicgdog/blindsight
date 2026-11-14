from types import SimpleNamespace

import numpy as np

from server.disambiguation import distinctive_support


def test_repeated_features_cannot_win_just_by_larger_raw_count():
    grid = np.array([(x, y) for x in (30, 60, 90, 330, 360) for y in (30, 60, 250, 280)])
    a = SimpleNamespace(idx=0, place="a", inliers=80)
    b = SimpleNamespace(idx=1, place="b", inliers=40)
    result = distinctive_support(
        a, [a, b], {0: np.tile(grid, (4, 1)), 1: np.tile(grid, (2, 1))}, (640, 480), 30
    )
    assert result["count"] == 0 and not result["passed"]


def test_distributed_distinctive_features_can_disambiguate():
    shared = np.array([(x, y) for x in range(10, 130, 20) for y in range(10, 130, 20)])
    unique = np.array([(x, y) for x in (250, 280, 310) for y in (40, 80, 280, 320)])
    a = SimpleNamespace(idx=0, place="a", inliers=48)
    b = SimpleNamespace(idx=1, place="b", inliers=36)
    result = distinctive_support(
        a, [a, b], {0: np.vstack([shared, unique]), 1: shared}, (640, 480), 30
    )
    assert result["count"] == 12 and result["passed"]


def test_missing_evidence_and_one_small_patch_fail_closed():
    a = SimpleNamespace(idx=0, place="a", inliers=100)
    b = SimpleNamespace(idx=1, place="b", inliers=40)
    assert not distinctive_support(a, [a, b], {}, (640, 480), 30)["passed"]
    own = np.array([(x, y) for x in range(10, 60, 5) for y in (10, 20)])
    assert not distinctive_support(a, [a, b], {0: own, 1: np.array([[500, 400]])}, (640, 480), 30)[
        "passed"
    ]


def test_same_place_views_are_not_rivals():
    a = SimpleNamespace(idx=0, place="a", inliers=100)
    b = SimpleNamespace(idx=1, place="a", inliers=100)
    assert distinctive_support(a, [a, b], {}, (640, 480), 30)["passed"]


def test_rejected_teaching_photos_do_not_rename_a_scan(tmp_path):
    from server.property_localize import Localizer

    folder = tmp_path / "1_wrong_name"
    folder.mkdir()
    (folder / "photo.jpg").touch()
    loc = Localizer.__new__(Localizer)
    loc.labels = {"p": {"label": "verified name"}}
    loc.resolve_folder = lambda _: ("p", "wrong name")
    loc.scan_name = lambda _: "1"
    loc._add_place_photo = lambda *_: None
    report = loc._load_photo_places(tmp_path)
    assert report[0]["added"] == 0
    assert loc.labels["p"]["label"] == "verified name"
