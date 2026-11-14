"""Part 3: the real Localizer end to end on the procedural room (contract + behaviour)."""

import dataclasses
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from geometry import SiftMatcher, ang_diff  # noqa: E402
from localize import LOCKED, ClassicEmbedder, Location, Localizer, Settings  # noqa: E402
from p3_room import Room, _texture, pos3, write_dataset  # noqa: E402

IDS = ["room_p0", "room_p1", "room_p3"]


@pytest.fixture(scope="module")
def room():
    return Room()


@pytest.fixture(scope="module")
def data(tmp_path_factory, room):
    return write_dataset(tmp_path_factory.mktemp("p3data"), room, S=384, ids=IDS)


def settings(**kw):
    return dataclasses.replace(Settings.load(calibration={}, use_config=False, use_env=False), **kw)


@pytest.fixture(scope="module")
def loc(data):
    return Localizer(data, settings=settings(), matcher=SiftMatcher(), embedder=ClassicEmbedder(), verbose=False)


def frame(room, pid, yaw, pitch=0.0, size=(540, 720)):
    return room.render(pos3(pid), yaw, pitch, 0, size, 55.0)


def test_contract_shapes(loc, room):
    loc.reset()
    out = loc.update(frame(room, "room_p0", 30), [], None)
    assert isinstance(out, Location)
    assert out.state in ("LOCKED", "UNCERTAIN", "LOST")
    assert out.pano_id == "room_p0" and out.label == "test room"
    assert isinstance(out.score, float) and isinstance(out.inliers, int)
    assert out.best_ref is not None and 0.0 <= out.yaw_conf <= 1.0
    assert set(loc.info()["competitors"]) == {"room_p1", "room_p3"}


def test_locks_at_the_scan_point_with_correct_yaw(loc, room):
    loc.reset()
    states, yaws = [], []
    for i in range(6):
        gt = 200 + 6 * i
        out = loc.update(frame(room, "room_p0", gt, 3 * np.sin(i)), [], (gt + 77.0) % 360)
        states.append(out.state)
        if out.state == LOCKED:
            yaws.append(ang_diff(out.yaw_deg, gt))
    assert states.index(LOCKED) <= 2  # locked by the second or third frame
    assert max(yaws) < 3.0


def test_never_locks_from_another_scan_point(loc, room):
    for pid in ("room_p1", "room_p3"):
        loc.reset()
        for i in range(8):
            out = loc.update(frame(room, pid, 60 + 15 * i), [], None)
            assert out.state != LOCKED, (pid, i, loc.last_debug)


def test_never_locks_on_an_unrelated_scene(loc):
    loc.reset()
    for i in range(6):
        img = _texture(540, 720, seed=900 + i, kind="wall")
        assert loc.update(img, [], None).state != LOCKED


def test_movable_object_boxes_do_not_break_locking(loc, room):
    loc.reset()
    person = [{"cls": "person", "conf": 0.9, "bbox": (0.35, 0.1, 0.65, 1.0)}]
    states = [loc.update(frame(room, "room_p0", 120 + 5 * i), person, None).state for i in range(4)]
    assert LOCKED in states


def test_lost_after_five_failures(loc, room):
    loc.reset()
    loc.update(frame(room, "room_p0", 10), [], None)
    loc.update(frame(room, "room_p0", 14), [], None)
    states = [loc.update(_texture(540, 720, seed=50 + i, kind="wall"), [], None).state for i in range(5)]
    assert states[-1] == "LOST" and "LOST" not in states[:-1]


def test_add_reference_verifies_before_adding(data, room):
    L = Localizer(data, settings=settings(), matcher=SiftMatcher(), embedder=ClassicEmbedder(), verbose=False)
    n0 = len(L._refs)
    assert L.add_reference(frame(room, "room_p0", 77, 4)) is True
    assert L.n_onsite == 1 and len(L._refs) == n0 + 1
    new = L._refs[-1].view
    assert new.source == "onsite" and ang_diff(new.yaw, 77) < 3
    assert L.add_reference(frame(room, "room_p3", 77)) is False  # another scan point
    assert L.add_reference(_texture(540, 720, seed=7, kind="wall")) is False  # unrelated
    assert L.n_onsite == 1
    L.reset()  # the on-site reference is used for retrieval and verification afterwards
    outs = [L.update(frame(room, "room_p0", 75 + i), [], None) for i in range(3)]
    assert outs[-1].state == LOCKED


def test_retrieval_only_mode_runs(data, room):
    L = Localizer(data, settings=settings(T_HI=-1.0), matcher=SiftMatcher(), embedder=ClassicEmbedder(),
                  mode="retrieval", verbose=False)
    out = [L.update(frame(room, "room_p0", 90 + i), [], None) for i in range(3)]
    assert out[-1].inliers == 0 and L.last_debug["verify"] == 0.0


def test_onsite_negative_frames_become_competing_views(tmp_path, room):
    """A scan point that is not a competing place can falsely lock (measured in the
    stress test); frames captured there via /api/capture/neg close that gap."""
    import cv2

    data = write_dataset(tmp_path, room, S=384, ids=["room_p0", "room_p1", "room_p3"])
    neg_dir = data / "negatives"
    neg_dir.mkdir()
    for k, yaw in enumerate(range(0, 360, 20)):
        img = room.render(pos3("room_p3"), yaw, 0, 0, (540, 720), 55.0)
        cv2.imwrite(str(neg_dir / f"cap_{k:03d}.jpg"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    L = Localizer(data, settings=settings(), matcher=SiftMatcher(), embedder=ClassicEmbedder(),
                  competitors=["room_p1", "onsite_negatives"], verbose=False)
    assert "onsite_negatives" in L.competitors and "room_p3" not in L.competitors
    for start in (95, 185, 275):
        L.reset()
        for i in range(6):
            assert L.update(frame(room, "room_p3", start + 4 * i), [], None).state != LOCKED
    L.reset()
    states = [L.update(frame(room, "room_p0", 140 + 4 * i), [], None).state for i in range(4)]
    assert LOCKED in states  # the locked pano still locks
    n = L.info()["n_competitor_refs"]
    L.add_negative(frame(room, "room_p3", 10))  # live capture, no restart
    assert L.info()["n_competitor_refs"] == n + 1
