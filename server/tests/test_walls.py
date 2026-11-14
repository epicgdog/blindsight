"""Wall outlines for the minimap, sliced out of the Matterport mesh."""

import json
import math

import numpy as np

from server.minimap import scan_points
from server.walls import parse_obj, slice_mesh, wall_segments

CUBE_OBJ = """
v 0 0 0
v 2 0 0
v 2 2 0
v 0 2 0
v 0 0 2
v 2 0 2
v 2 2 2
v 0 2 2
f 1 2 3 4
f 5 6 7 8
f 1 2 6 5
f 2 3 7 6
f 3 4 8 7
f 4 1 5 8
"""


def total_length(segs):
    return sum(math.hypot(x2 - x1, y2 - y1) for x1, y1, x2, y2 in segs)


def test_parse_obj_triangulates_quads_and_ignores_other_lines():
    v, f = parse_obj("# comment\nvn 0 0 1\n" + CUBE_OBJ)
    assert v.shape == (8, 3) and f.shape == (12, 3)  # 6 quads become 12 triangles


def test_parse_obj_handles_slashed_face_indices():
    v, f = parse_obj("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1/1/1 2/2/2 3/3/3\n")
    assert f.tolist() == [[0, 1, 2]]


def test_slicing_a_box_at_mid_height_gives_its_footprint():
    v, f = parse_obj(CUBE_OBJ)
    segs = slice_mesh(v, f, 1.0)
    assert math.isclose(total_length(segs), 8.0, abs_tol=1e-6)  # perimeter of a 2 x 2 square
    xs = [p for s in segs for p in (s[0], s[2])]
    assert min(xs) == 0 and max(xs) == 2


def test_slice_above_or_below_the_mesh_is_empty():
    v, f = parse_obj(CUBE_OBJ)
    assert slice_mesh(v, f, 5.0) == []
    assert slice_mesh(v, f, -1.0) == []


def test_wall_segments_drop_specks_and_round():
    v, f = parse_obj(CUBE_OBJ)
    segs = wall_segments(v, f, 1.0, min_len=0.05)
    assert all(len(s) == 4 and all(round(c, 2) == c for c in s) for s in segs)
    tiny = np.array([[0, 0, 0], [0.01, 0, 0], [0, 0.01, 0], [0, 0, 2], [0.01, 0, 2], [0, 0.01, 2]], float)
    faces = np.array([[0, 1, 4], [0, 4, 3], [0, 2, 5], [0, 5, 3]])
    assert wall_segments(tiny, faces, 1.0, min_len=0.05) == []


def write_walls(tmp_path, segments):
    (tmp_path / "matterport").mkdir(exist_ok=True)
    (tmp_path / "matterport" / "walls_floor1.json").write_text(
        json.dumps({"floor": 0, "z": 1.0, "segments": segments})
    )


def test_scan_points_carry_walls_near_the_floor_one_points(tmp_path):
    (tmp_path / "matterport").mkdir()
    (tmp_path / "matterport" / "panoLocations.json").write_text(
        json.dumps({"panoLocations": [
            {"id": "a", "label": "0", "position": {"x": 0, "y": 0, "z": 1.3}},
            {"id": "b", "label": "1", "position": {"x": 10, "y": 5, "z": 1.3}},
        ]})
    )
    near = [0, 0, 5, 0]
    far = [200, 200, 210, 200]  # nowhere near the scan points
    write_walls(tmp_path, [near, far])
    out = scan_points(tmp_path)
    assert out["walls"] == [near]


def test_no_walls_file_means_no_walls(tmp_path):
    (tmp_path / "matterport").mkdir()
    (tmp_path / "matterport" / "panoLocations.json").write_text(
        json.dumps({"panoLocations": [{"id": "a", "label": "0", "position": {"x": 0, "y": 0, "z": 1.3}}]})
    )
    assert scan_points(tmp_path)["walls"] == []
