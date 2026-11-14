"""Part 3: skybox face layout checks and the 72 reference views."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from p3_room import Room, pos3, write_dataset  # noqa: E402
from refs import (  # noqa: E402
    REF_PITCHES,
    REF_YAWS,
    CubeLayout,
    CubeSampler,
    build_and_save,
    build_refs,
    check_pano,
    detect_layout,
    load_refs,
    pano_info,
)


@pytest.fixture(scope="module")
def room():
    return Room()


def test_default_layout_is_detected(room):
    lay, rep = detect_layout(room.faces(pos3("room_p0"), 256))
    assert rep["ok"] and not rep["upside_down_suspected"]
    assert lay.horizon_yaws == (0.0, 90.0, 180.0, 270.0) and lay.up_yaw == 0 and lay.down_yaw == 0


def test_permuted_layout_is_recovered_not_assumed(room):
    truth = CubeLayout(horizon_yaws=(0.0, 270.0, 180.0, 90.0), up_yaw=90.0, down_yaw=180.0)
    faces = room.faces(pos3("room_p0"), 256, truth)
    lay, rep = detect_layout(faces)
    assert rep["ok"]
    assert (lay.horizon_yaws, lay.up_yaw, lay.down_yaw) == (truth.horizon_yaws, 90.0, 180.0)
    direct = room.render(pos3("room_p0"), 33, 5, 0, (180, 240), 55)
    via_cube = CubeSampler(faces, lay).render(33, 5, 0, (180, 240), 55)
    assert np.abs(direct.astype(int) - via_cube.astype(int)).mean() < 8


def test_upside_down_faces_are_flagged(room):
    faces = room.faces(pos3("room_p0"), 256)
    flipped = [faces[0]] + [np.ascontiguousarray(f[::-1, ::-1]) for f in faces[1:5]] + [faces[5]]
    _, rep = detect_layout(flipped)
    assert rep["upside_down_suspected"] and not rep["ok"]


def test_72_upright_views_cover_the_full_circle(room):
    refs = build_refs(CubeSampler(room.faces(pos3("room_p0"), 256)), 55.0, (90, 120))
    assert len(refs) == 72 == len(REF_YAWS) * len(REF_PITCHES)
    assert sorted({r.yaw for r in refs}) == list(range(0, 360, 15))
    assert sorted({r.pitch for r in refs}) == [-10, 0, 10]
    for r in refs:  # upright and correctly aimed: matches a ground-truth render, not its flip
        gt = room.render(pos3("room_p0"), r.yaw, r.pitch, 0, (90, 120), 55.0).astype(int)
        err = np.abs(r.image.astype(int) - gt).mean()
        err_flipped = np.abs(r.image[::-1, ::-1].astype(int) - gt).mean()
        assert err < 8 and err_flipped > 2 * err, r.name


def test_build_save_load_and_pano_info(tmp_path, room):
    write_dataset(tmp_path, room, S=192, ids=["room_p0", "room_p1"])
    pdir = tmp_path / "pano" / "room_p0"
    rep = check_pano(pdir)
    assert rep["ok"]
    for f in ("contact_faces.jpg", "contact_cross.jpg", "equirect.jpg", "horizon.jpg", "layout.json"):
        assert (pdir / f).exists()
    build_and_save(pdir, 55.0, (90, 120))
    refs, meta = load_refs(pdir / "refs")
    assert len(refs) == 72 and meta["hfov"] == 55.0
    info = pano_info(tmp_path, {"N_MIN": 30})
    assert info["pano_id"] == "room_p0" and info["label"] == "test room"
    assert info["faces"] == [f"pano/room_p0/face_{i}.jpg" for i in range(6)]
    assert len(info["refs"]) == 72 and info["layout_verified"]
