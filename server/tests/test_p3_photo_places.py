"""Part 3: phone photos in data/onsite/places/<scan>_<name>/ teach the property localizer places."""

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p3_room import Room, _texture, pos3, write_dataset  # noqa: E402

from server.geometry import SiftMatcher, ang_diff  # noqa: E402
from server.localize import ClassicEmbedder, Settings  # noqa: E402
from server.property_localize import Localizer, try_places  # noqa: E402

IDS = ["room_p0", "room_p1", "room_p3"]
TAUGHT_YAWS = (20, 110, 200, 290)


def _kwargs():
    return dict(
        settings=Settings.load(calibration={}, use_config=False, use_env=False),
        matcher=SiftMatcher(),
        embedder=ClassicEmbedder(),
        verbose=False,
    )


def _save(path, rgb):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 90])


def _photo(room, place, yaw):
    return room.render(pos3(place), yaw, 0, 0, (540, 720), 55.0)


@pytest.fixture(scope="module")
def taught(tmp_path_factory):
    room = Room()
    root = write_dataset(tmp_path_factory.mktemp("places"), room, S=384, ids=IDS)
    (root / "matterport").mkdir(exist_ok=True)
    manifest = {"data": {"model": {"panoLocations": [{"id": p, "label": p[-1]} for p in IDS]}}}
    (root / "matterport" / "panoLocations.json").write_text(json.dumps(manifest))
    places = root / "onsite" / "places"
    for k, yaw in enumerate(TAUGHT_YAWS):
        _save(places / "1_reading_corner" / f"IMG_{k}.jpg", _photo(room, "room_p1", yaw))
    _save(places / "1_reading_corner" / "zz_taken_elsewhere.jpg", _photo(room, "room_p3", 20))
    _save(places / "1_reading_corner" / "zz_blank.jpg", _texture(540, 720, seed=3, kind="wall"))
    _save(places / "99_nowhere" / "IMG_0.jpg", _photo(room, "room_p1", 20))
    return room, root, Localizer(root, **_kwargs())


def test_folder_name_resolves_scan_number_and_spoken_name(taught):
    _, _, loc = taught
    assert loc.resolve_folder("1_reading_corner") == ("room_p1", "reading corner")
    assert loc.resolve_folder("room_p3_back-door") == ("room_p3", "back door")
    assert loc.resolve_folder("3") == ("room_p3", None)
    assert loc.resolve_folder("99_nowhere") == (None, None)


def test_photos_are_added_to_their_scan_with_pano_frame_yaw(taught):
    _, _, loc = taught
    report = {e["folder"]: e for e in loc.photo_places}
    corner = report["1_reading_corner"]
    assert corner["pano_id"] == "room_p1" and corner["name"] == "reading corner"
    assert corner["added"] == len(TAUGHT_YAWS)
    assert sorted(corner["skipped"]) == ["zz_blank.jpg", "zz_taken_elsewhere.jpg"]
    assert report["99_nowhere"]["pano_id"] is None and report["99_nowhere"]["added"] == 0
    photos = [r for r in loc._refs if r.view.source == "photo"]
    assert {r.place for r in photos} == {"room_p1"}
    for r, yaw in zip(sorted(photos, key=lambda r: r.view.name), TAUGHT_YAWS, strict=True):
        assert ang_diff(r.view.yaw, yaw) < 3, (r.view.name, r.view.yaw)
    assert len(loc._refs) == loc._emb.shape[0] == len(loc._is_demo) == len(loc._is_pano_demo)
    assert loc.place_label("room_p1") == "reading corner"
    assert any("99_nowhere: no scan number" in line for line in loc.photo_places_summary())
    assert loc.info()["photo_places"][0]["added"] == len(TAUGHT_YAWS)


def test_taught_place_locks_and_speaks_its_name(taught):
    room, _, loc = taught
    loc.reset()
    out = None
    for i in range(4):
        out = loc.update(_photo(room, "room_p1", 60 + 3 * i), [], None)
    assert out.state == "LOCKED", loc.last_debug
    assert out.pano_id == "room_p1" and out.label == "reading corner"
    assert ang_diff(out.yaw_deg, 69) < 4


def test_try_places_reports_right_and_correctly_unmatched(taught, tmp_path, capsys):
    room, root, _ = taught
    test = tmp_path / "test_photos"
    for k, yaw in enumerate((45, 160)):
        _save(test / "1_reading_corner" / f"T_{k}.jpg", _photo(room, "room_p1", yaw))
    _save(test / "elsewhere" / "E_0.jpg", _texture(540, 720, seed=11, kind="wall"))
    assert try_places(root, test, **_kwargs()) == 0
    out = capsys.readouterr().out
    rows = [ln for ln in out.splitlines() if ln.startswith(("1_reading_corner/", "elsewhere/"))]
    assert len(rows) == 3
    assert all(" right " in r for r in rows if r.startswith("1_reading_corner/")), out
    assert " ok " in next(r for r in rows if r.startswith("elsewhere/")), out
    assert (
        "3 photos: 2 right, 0 wrong place, 0 missed, 1 correctly not matched, 0 false matches."
        in out
    )
    assert "scan 1 reading corner" in out


def test_teaching_photos_used_as_test_photos_warn(taught, capsys):
    _, root, _ = taught
    try_places(root, root / "onsite" / "places", **_kwargs())
    assert "these are the teaching photos themselves" in capsys.readouterr().out


def test_no_places_folder_changes_nothing(tmp_path):
    room = Room()
    root = write_dataset(tmp_path, room, S=384, ids=IDS)
    loc = Localizer(root, **_kwargs())
    assert loc.photo_places == [] and not any(r.view.source == "photo" for r in loc._refs)
    assert np.isfinite(loc._emb).all()


def test_verbose_startup_prints_taught_places(taught, capsys):
    """The server builds the localizer with verbose=True; startup must not fail."""
    _, root, _ = taught
    kwargs = _kwargs() | {"verbose": True}
    Localizer(root, **kwargs)
    err = capsys.readouterr().err
    assert "[property] 1_reading_corner: 4 photos added to scan 1 as 'reading corner'" in err
    assert "2 skipped" in err
