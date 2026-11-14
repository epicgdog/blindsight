"""Part 3: scripts/fetch_matterport.py against a response shaped like the quickstart's
Panos query (section 4), with file:// face links instead of signed CDN links."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_matterport as fm  # noqa: E402
from p3_room import POSITIONS, Room, write_dataset  # noqa: E402


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(fm, "DATA", data)
    monkeypatch.setattr(fm, "MP_DIR", data / "matterport")
    monkeypatch.setattr(fm, "PANO_DIR", data / "pano")
    monkeypatch.setattr(fm, "API_CFG", ROOT / "data" / "matterport" / "api.json")
    monkeypatch.setattr(fm, "ENV_FILE", tmp_path / ".env.local")
    for k in ("MATTERPORT_API_KEY", "MATTERPORT_API_TOKEN", "P3_AUTH_VIA_PROXY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def fake_response(src: Path, ids: list[str]) -> dict:
    """Same keys as the quickstart's Panos response: label is a scan number, z is up."""
    panos = []
    for n, pid in enumerate(ids):
        x, y = POSITIONS[pid]
        panos.append({"id": pid, "label": str(10 + n), "position": {"x": x, "y": y, "z": 1.5},
                      "skybox": {"status": "available",
                                 "children": [f"file://{src}/pano/{pid}/face_{k}.jpg" for k in range(6)]}})
    return {"data": {"model": {"name": "Test Lobby", "panoLocations": panos}}}


def test_fetch_ranks_downloads_and_writes_pano_json(sandbox):
    ids = ["room_p0", "room_p1", "room_p2", "room_p3"]
    src = write_dataset(sandbox / "src", Room(), S=128, ids=ids)
    raw = sandbox / "panos.json"
    raw.write_text(json.dumps(fake_response(src, ids)))
    assert fm.main(["--from-json", str(raw), "--neighbors", "2", "--candidates", "2",
                    "--label", "the test lobby"]) == 0
    meta = json.loads((sandbox / "data" / "pano" / "pano.json").read_text())
    assert meta["label"] == "the test lobby" and meta["scan_label"] in {"10", "11", "12", "13"}
    assert meta["model_name"] == "Test Lobby" and meta["vertical_axis"] == "z"
    assert len(meta["negatives"]) == 2
    assert meta["negatives"][0]["dist_m"] <= meta["negatives"][1]["dist_m"]
    pdir = sandbox / "data" / "pano" / meta["pano_id"]
    assert all((pdir / f"face_{k}.jpg").exists() for k in range(6))
    assert json.loads((pdir / "layout.json").read_text())["ok"]
    assert (sandbox / "data" / "matterport" / "topdown.png").exists()


def test_scan_numbers_are_never_spoken():
    assert fm.spoken_label({"label": "22"}, None) is None
    assert fm.spoken_label({"label": "Main Lobby"}, None) == "Main Lobby"
    assert fm.spoken_label({"label": "22"}, "the main lobby") == "the main lobby"


def test_saved_responses_have_signatures_removed():
    out = fm.redact_signatures({"a": ["https://cdn-2.matterport.com/x/skybox0.jpg?t=SECRET&e=1"], "b": "keep?me"})
    assert out["a"][0] == "https://cdn-2.matterport.com/x/skybox0.jpg?<signature removed>"
    assert out["b"] == "keep?me"


def test_query_variables_follow_the_configured_name():
    cfg = json.loads((ROOT / "data" / "matterport" / "api.json").read_text())
    assert fm.model_vars(cfg) == {"id": fm.MODEL_ID}
    assert "$id: ID!" in cfg["pano_query"] and "skybox(resolution: \"2k\")" in cfg["pano_query"]


def test_missing_credentials_stop_the_script(sandbox):
    with pytest.raises(SystemExit) as e:
        fm.main([])
    assert e.value.code == fm.EXIT_NO_CREDENTIALS


def square(fid: str, name: str, x0: float, y0: float, x1: float, y1: float, conf: float = 0.9):
    """One room as the Rooms query returns it: wall vertices, wall edges (shuffled), boundary."""
    v = [(f"{name}{i}", x, y) for i, (x, y) in enumerate([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])]
    e = [(f"e{name}{i}", v[i][0], v[(i + 1) % 4][0]) for i in range(4)]
    e = [e[2], e[0], e[3], e[1]]  # boundary edges in no particular order
    verts = [{"id": i, "position": {"x": x, "y": y}} for i, x, y in v]
    edges = [{"id": i, "vertices": [{"id": a}, {"id": b}]} for i, a, b in e]
    room = {"id": f"r{name}", "floor": {"id": fid}, "classifications": [{"label": name, "confidence": conf}],
            "boundary": {"edges": [{"id": i} for i, _, _ in e]}}
    return verts, edges, room


def fake_rooms() -> dict:
    parts = [square("f1", "Lobby", -4.0, 0.0, -2.0, 2.0), square("f1", "Office", 1.5, -1.0, 4.0, 2.0),
             square("f1", "Void", -1.0, -1.0, 1.0, 1.0)]
    floor = {"id": "f1", "label": "Floor 1", "vertices": [x for p in parts for x in p[0]],
             "edges": [x for p in parts for x in p[1]]}
    return {"data": {"model": {"floors": [floor], "rooms": [p[2] for p in parts]}}}


def room_panos():
    return [{"id": pid, "label": str(n), "pos": [*POSITIONS[pid], 1.5]}
            for n, pid in enumerate(["room_p0", "room_p1", "room_p2", "room_p3"])]


def test_scan_points_get_room_names_and_lobby_ranks_first():
    panos = room_panos()
    fm.assign_rooms(panos, fm.room_polygons(fake_rooms()))
    rooms = {p["id"]: p["room"] for p in panos}
    # p2 (-3, 1) is in the Lobby, p1 (2.5, 0.5) in the Office; "Void" rooms are ignored
    assert rooms == {"room_p0": None, "room_p1": "Office", "room_p2": "Lobby", "room_p3": None}
    assert fm.rank(panos)[0]["id"] == "room_p2"  # a lobby beats a more central scan point
    lobby = next(p for p in panos if p["id"] == "room_p2")
    assert fm.spoken_label(lobby, None) == "the lobby"
    assert fm.spoken_label(lobby, "Rabinowitz Hall") == "Rabinowitz Hall"
    assert fm.spoken_label({"label": "3", "room": "Lobby / Office"}, None) is None  # ambiguous: two floors


def test_list_prints_rooms_and_downloads_nothing(sandbox, monkeypatch, capsys):
    ids = ["room_p0", "room_p1", "room_p2", "room_p3"]
    monkeypatch.setenv("MATTERPORT_API_KEY", "dummy")
    monkeypatch.setenv("MATTERPORT_API_TOKEN", "dummy")
    cfg = json.loads((ROOT / "data" / "matterport" / "api.json").read_text())

    def fake_graphql(_cfg, headers, query, variables):
        assert headers["Authorization"].startswith("Basic ") and variables == {"id": "XYZ"}
        if query == cfg["rooms_query"]:
            return fake_rooms()
        return fake_response(sandbox, ids)

    monkeypatch.setattr(fm, "graphql", fake_graphql)
    assert fm.main(["--list", "--model", "XYZ"]) == 0
    out = capsys.readouterr().out
    assert "Lobby" in out and "Office" in out and "room_p3" in out
    assert not (sandbox / "data" / "pano").exists()
