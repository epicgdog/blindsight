"""Scan-point data for the viewer's minimap."""

import json

from server.minimap import scan_points


def write(tmp_path, panos, pano_json=None):
    (tmp_path / "matterport").mkdir()
    (tmp_path / "matterport" / "panoLocations.json").write_text(
        json.dumps({"data": {"model": {"name": "M", "panoLocations": panos}}})
    )
    if pano_json is not None:
        (tmp_path / "pano").mkdir()
        (tmp_path / "pano" / "pano.json").write_text(json.dumps(pano_json))


def pano(pid, label, x, y, z):
    return {
        "id": pid,
        "label": label,
        "position": {"x": x, "y": y, "z": z},
        "skybox": {"status": "available"},
    }


def test_points_carry_position_label_and_floor(tmp_path):
    write(
        tmp_path,
        [
            pano("a", "0", 1.0, 2.0, 1.36),
            pano("b", "5", 3.0, 4.0, 1.4),
            pano("c", "9", 0.0, 0.0, 5.3),
        ],
    )
    out = scan_points(tmp_path)
    by_id = {p["id"]: p for p in out["points"]}
    assert by_id["a"]["x"] == 1.0 and by_id["a"]["y"] == 2.0 and by_id["a"]["label"] == "0"
    assert by_id["a"]["floor"] == by_id["b"]["floor"] == 0
    assert by_id["c"]["floor"] == 1


def test_floors_are_split_by_height_gaps(tmp_path):
    zs = [1.36, 1.4, 3.4, 5.3, 5.5]
    write(tmp_path, [pano(f"p{i}", str(i), float(i), 0.0, z) for i, z in enumerate(zs)])
    out = scan_points(tmp_path)
    assert [p["floor"] for p in out["points"]] == [0, 0, 1, 2, 2]
    assert [f["count"] for f in out["floors"]] == [2, 1, 2]


def test_room_names_come_from_pano_json(tmp_path):
    write(
        tmp_path,
        [pano("a", "0", 0, 0, 1.3), pano("b", "7", 1, 1, 1.3)],
        {
            "pano_id": "a",
            "room": "Hallway",
            "scan_label": "0",
            "negatives": [{"pano_id": "b", "label": "7", "room": "Stairwell"}],
        },
    )
    rooms = {p["id"]: p["room"] for p in scan_points(tmp_path)["points"]}
    assert rooms == {"a": "Hallway", "b": "Stairwell"}


def test_bounds_cover_all_points(tmp_path):
    write(tmp_path, [pano("a", "0", -2, 5, 1.3), pano("b", "1", 8, -1, 1.3)])
    b = scan_points(tmp_path)["bounds"]
    assert (b["min_x"], b["max_x"], b["min_y"], b["max_y"]) == (-2, 8, -1, 5)


def test_missing_or_broken_file_gives_no_points(tmp_path):
    assert scan_points(tmp_path)["points"] == []
    (tmp_path / "matterport").mkdir()
    (tmp_path / "matterport" / "panoLocations.json").write_text("not json")
    assert scan_points(tmp_path)["points"] == []


def test_plan_info_is_included_when_a_floor_plan_was_downloaded(tmp_path):
    write(tmp_path, [pano("a", "0", 0.0, 0.0, 1.3)])
    meta = {"width": 2500, "height": 2000, "resolution": 80.0, "origin": {"x": -10.0, "y": -12.0}, "floor": 0,
            "label": "Floor 1", "file": "floor1.png"}
    (tmp_path / "matterport" / "floorplan_floor1.json").write_text(json.dumps(meta))
    (tmp_path / "matterport" / "floor1.png").write_bytes(b"png")
    plan = scan_points(tmp_path)["plan"]
    assert plan == {"url": "/api/floorplan/0", "width": 2500, "height": 2000, "resolution": 80.0,
                    "origin": {"x": -10.0, "y": -12.0}, "floor": 0, "label": "Floor 1"}


def test_no_plan_without_the_image_file(tmp_path):
    write(tmp_path, [pano("a", "0", 0.0, 0.0, 1.3)])
    (tmp_path / "matterport" / "floorplan_floor1.json").write_text(json.dumps({"width": 1, "height": 1,
        "resolution": 1.0, "origin": {"x": 0, "y": 0}, "floor": 0, "file": "floor1.png"}))
    assert scan_points(tmp_path)["plan"] is None
