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


def test_try_photos_reports_each_photo(data, room, tmp_path, monkeypatch, capsys):
    import cv2

    import localize

    monkeypatch.setenv("P3_MATCHER", "sift")
    monkeypatch.setenv("P3_RETRIEVER", "classic")
    shots = tmp_path / "photos"
    shots.mkdir()
    for k, (pid, yaw) in enumerate([("room_p0", 40), ("room_p0", 200), ("room_p3", 40)]):
        img = room.render(pos3(pid), yaw, 0, 0, (540, 720), 55.0)
        cv2.imwrite(str(shots / f"IMG_{k}.jpg"), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    assert localize.main(["--try", str(shots), "--data-dir", str(data)]) == 0
    out = capsys.readouterr().out
    rows = [ln for ln in out.splitlines() if ln.startswith("IMG_")]
    assert [r.split()[1] for r in rows] == ["YES", "YES", "no"]
    assert "2 of 3 photos matched" in out


def test_match_view_ranks_places_and_outlines_the_frame_on_the_pano(loc, room):
    """The viewer's live ranking and footprint box come from last_debug["match"]."""
    loc.reset()
    gt = 200.0
    for i in range(3):
        out = loc.update(frame(room, "room_p0", gt + 5 * i), [], (gt + 77.0) % 360)
    match = loc.last_debug["match"]
    ranking = match["ranking"]
    assert ranking and ranking[0]["place"] == "room_p0"  # the true scan point ranks first
    assert ranking == sorted(ranking, key=lambda r: -r["sim"])
    assert len({r["place"] for r in ranking}) == len(ranking)
    if out.state == LOCKED:
        poly = match["footprint"]
        assert poly and all(len(p) == 2 for p in poly)
        cx = sum(p[0] for p in poly) / len(poly)
        # the box is centred near the matched yaw (x = yaw / 360, unwrapped)
        assert abs(((cx * 360 - out.yaw_deg + 180) % 360) - 180) < 8


def _fake_ref_detector(image):
    """A 'person' over the centre third of every reference view."""
    from types import SimpleNamespace

    return [SimpleNamespace(cls="person", conf=0.9, bbox=(0.33, 0.33, 0.67, 0.67))]


@pytest.fixture(scope="module")
def masked_loc(data):
    return Localizer(
        data, settings=settings(), matcher=SiftMatcher(), embedder=ClassicEmbedder(),
        verbose=False, ref_detector=_fake_ref_detector, ref_detector_id="fake@0.35",
    )


def test_reference_views_are_masked_before_matching(masked_loc):
    demo = [r for r in masked_loc._refs if r.demo and r.view.source == "pano"]
    assert demo and all(r.masks for r in demo)
    r = demo[0]
    x1, y1, x2, y2 = r.masks[0]
    kp = r.feats.kpts
    inside = (kp[:, 0] >= x1) & (kp[:, 0] <= x2) & (kp[:, 1] >= y1) & (kp[:, 1] <= y2)
    assert not inside.any(), "no keypoints may sit on a masked person"


def test_masking_changes_the_reference_embeddings(loc, masked_loc):
    assert loc._emb.shape == masked_loc._emb.shape
    assert not np.allclose(loc._emb, masked_loc._emb)


def test_unmasked_localizer_is_unchanged(loc):
    assert all(r.masks == [] for r in loc._refs)
    assert loc.masked_regions() == []


def test_masked_regions_outline_the_ignored_areas_on_the_pano(masked_loc):
    polys = masked_loc.masked_regions()
    assert polys and all(len(p) >= 4 and all(len(pt) == 2 for pt in p) for p in polys)


def test_masked_localizer_still_locks_at_the_scan_point(masked_loc, room):
    masked_loc.reset()
    states = []
    for i in range(6):
        gt = 200 + 6 * i
        out = masked_loc.update(frame(room, "room_p0", gt), [], (gt + 77.0) % 360)
        states.append(out.state)
    assert LOCKED in states


# ---- multi-place mode: every pano is a candidate place -----------------------------------


@pytest.fixture(scope="module")
def multi(data):
    return Localizer(
        data, settings=settings(), matcher=SiftMatcher(), embedder=ClassicEmbedder(),
        verbose=False, places="all",
    )


def test_every_pano_is_a_candidate_place_in_multi_mode(multi):
    assert set(multi.candidate_places) == set(IDS)
    assert all(r.demo for r in multi._refs if r.view.source == "pano")
    assert multi.competitors == []  # nothing left to be only a distractor


def test_single_mode_keeps_one_candidate_place(loc):
    assert loc.candidate_places == ["room_p0"]


def _lock_at(L, room, pid, yaw0=200, n=6):
    L.reset()
    out = None
    for i in range(n):
        gt = yaw0 + 6 * i
        out = L.update(frame(room, pid, gt), [], (gt + 77.0) % 360)
    return out


@pytest.mark.parametrize("pid", IDS)
def test_multi_mode_reports_the_place_the_frames_came_from(multi, room, pid):
    out = _lock_at(multi, room, pid)
    assert out.state == LOCKED
    assert out.pano_id == pid
    assert out.label  # a speakable place name


def test_multi_mode_ranking_puts_the_true_place_first(multi, room):
    _lock_at(multi, room, "room_p3")
    ranking = multi.last_debug["match"]["ranking"]
    assert ranking[0]["place"] == "room_p3"


def test_place_does_not_switch_on_a_single_frame(multi, room):
    out = _lock_at(multi, room, "room_p0")
    assert out.pano_id == "room_p0"
    one = multi.update(frame(room, "room_p3", 230), [], 307.0)
    assert one.pano_id == "room_p0", "one frame from elsewhere must not move the place"


def test_place_switches_after_consistent_frames(multi, room):
    _lock_at(multi, room, "room_p0")
    out = None
    for i in range(8):
        gt = 220 + 6 * i
        out = multi.update(frame(room, "room_p3", gt), [], (gt + 77.0) % 360)
    assert out.pano_id == "room_p3" and out.state == LOCKED


def test_embeddings_are_cached_per_place(data):
    calls = []

    class Counting(ClassicEmbedder):
        def embed_batch(self, imgs):
            calls.append(len(imgs))
            return super().embed_batch(imgs)

    for cached in (data / "pano").glob("*/cache/emb_*.npy"):
        cached.unlink()  # earlier fixtures already filled the cache
    kw = dict(settings=settings(), matcher=SiftMatcher(), embedder=Counting(), verbose=False, places="all")
    Localizer(data, **kw)
    first = sum(calls)
    calls.clear()
    Localizer(data, **kw)
    assert sum(calls) < first, "second start must reuse cached reference embeddings"


# ---- IMU pitch gate: a match must agree with the phone's measured tilt ---------------------


def test_correct_imu_pitch_still_locks(loc, room):
    loc.reset()
    states = []
    for i in range(6):
        gt = 200 + 6 * i
        out = loc.update(frame(room, "room_p0", gt), [], (gt + 77.0) % 360, pitch_deg=0.0, roll_deg=0.0)
        states.append(out.state)
    assert LOCKED in states


def test_match_that_disagrees_with_imu_pitch_is_rejected(loc, room):
    loc.reset()
    states = []
    for i in range(6):
        gt = 200 + 6 * i
        # the image is level (pitch 0) but the phone claims it points 40 degrees down
        out = loc.update(frame(room, "room_p0", gt), [], (gt + 77.0) % 360, pitch_deg=-40.0, roll_deg=0.0)
        states.append(out.state)
    assert LOCKED not in states
    assert loc.last_debug["imu_rejected"] is True


def test_no_imu_means_no_gate(loc, room):
    loc.reset()
    loc.update(frame(room, "room_p0", 200), [], 277.0)
    assert loc.last_debug["imu_rejected"] is False


def test_reports_metric_position_off_the_scan_point(loc, room):
    """With a depth source attached, a matched frame carries the camera centre (depth + PnP)."""
    from p3_room import HI, LO
    from position import BoxDepth, PlaceFrame, PositionSolver

    saved = loc.position
    loc.position = PositionSolver(BoxDepth(LO, HI), {pid: PlaceFrame(pos3(pid)) for pid in IDS})
    try:
        offset = np.array([0.25, 0.0, -0.2])
        errs = []
        for yaw in (30, 120, 250):
            loc.reset()
            loc.update(room.render(pos3("room_p0") + offset, yaw, 0, 0, (540, 720), 55.0), [], None)
            p = (loc.last_debug.get("match") or {}).get("position")
            assert loc.last_debug["matched"] and p is not None, loc.last_debug
            assert p["place"] == "room_p0"
            errs.append(float(np.linalg.norm(np.array([p["x"], p["y"], p["z"]]) - (pos3("room_p0") + offset))))
        assert max(errs) < 0.25, errs
    finally:
        loc.position = saved


def test_position_absent_without_depth(loc, room):
    saved, loc.position = loc.position, None
    try:
        loc.reset()
        loc.update(frame(room, "room_p0", 30), [], None)
        assert loc.last_debug["position"] is None
        assert "position" not in (loc.last_debug.get("match") or {})
    finally:
        loc.position = saved
