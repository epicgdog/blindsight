import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p3_room import Room, pos3, write_dataset

from server.geometry import SiftMatcher
from server.localize import ClassicEmbedder, Settings
from server.property_localize import Localizer, choose_place


def candidate(place, inliers=50, grid=5, yaw=0):
    return SimpleNamespace(place=place, inliers=inliers, grid=grid, yaw=yaw)


def test_ambiguous_and_unknown_places_fail_closed():
    settings = Settings()
    assert choose_place([candidate("a"), candidate("b")], settings)[0] is None
    assert choose_place([candidate("onsite_negatives", 100), candidate("b")], settings)[0] is None
    assert choose_place([candidate("a", grid=1)], settings)[0] is None
    assert choose_place([candidate("a", yaw=None)], settings)[0] is None
    assert choose_place([candidate("a", 100), candidate("b", 40)], settings)[0].place == "a"


def test_incomplete_property_fails_before_loading_models(tmp_path):
    import json

    for place in ("a", "b"):
        directory = tmp_path / "pano" / place
        directory.mkdir(parents=True)
        for face in range(6):
            (directory / f"face_{face}.jpg").touch()
    manifest = tmp_path / "matterport" / "panoLocations.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps({"data": {"model": {"panoLocations": [{"id": p} for p in ("a", "b", "c")]}}})
    )
    with pytest.raises(ValueError, match="missing 1 of 3"):
        Localizer(tmp_path)


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    room = Room()
    root = write_dataset(
        tmp_path_factory.mktemp("property"), room, S=384, ids=["room_p0", "room_p1", "room_p3"]
    )
    localizer = Localizer(
        root,
        settings=Settings.load(calibration={}, use_config=False, use_env=False),
        matcher=SiftMatcher(),
        embedder=ClassicEmbedder(),
        verbose=False,
    )
    return room, localizer


def test_recognizes_multiple_places_and_changes_location(scene):
    room, localizer = scene
    for place in ["room_p0", "room_p1", "room_p3", "room_p0"]:
        for frame in range(4):
            image = room.render(pos3(place), 30 + frame, 0, 0, (360, 480), 55)
            result = localizer.update(image, [], None)
            if frame == 0:
                assert result.state != "LOCKED"
                assert result.pano_id is None
        assert result.state == "LOCKED", localizer.last_debug
        assert result.pano_id == place
        assert localizer.last_debug["indexed_scans"] == 3


def test_reset_and_unrelated_image_never_retain_old_location(scene):
    import numpy as np

    _, localizer = scene
    localizer.reset()
    result = localizer.update(np.zeros((480, 360, 3), dtype=np.uint8), [], None)
    assert result.pano_id is None and result.state != "LOCKED"
    assert localizer.add_reference(np.zeros((480, 360, 3), dtype=np.uint8)) is False
