import json
import math
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.agent import AgentState, GeminiAgent, agent_router, parse_reply
from server.cues import CueEngine
from server.entrance_nav import EntranceGuide, EntranceMap, clear_line, clock, mesh_heading_deg, shortest_path
from server.place_window import PlaceWindow, frame_confidence
from server.protocol import Location
from server.scene_memory import SceneMemory


def rank(*rows):
    return [{"place": p, "inliers": i, "sim": 0.9} for p, i in rows]


# ---- place window -------------------------------------------------------------------
def test_confidence_rewards_strength_and_punishes_ties():
    assert frame_confidence(rank(("a", 70))) == ("a", pytest.approx(0.7))
    place, tied = frame_confidence(rank(("a", 100), ("b", 100)))
    assert tied < 0.4
    assert frame_confidence(rank(("a", 0))) == (None, 0.0)
    assert frame_confidence(None) == (None, 0.0)


def test_window_picks_highest_confident_frame_then_holds_then_unknown():
    w = PlaceWindow()
    assert w.push(0.0, rank(("a", 200), ("b", 10))).place == "a"
    d = w.push(1.0, rank(("b", 300), ("a", 5)))
    assert (d.place, d.state) == ("b", "confident")  # highest confidence in the window wins
    w.push(6.5, rank(("c", 20)))  # weak frames only; old frames left the 5 s window
    d = w.decide(6.5)
    assert (d.place, d.state) == ("b", "holding")
    d = w.push(11.5, rank(("c", 20)))
    assert (d.place, d.state) == (None, "unknown")


def test_window_ignores_frames_at_or_below_threshold():
    w = PlaceWindow()
    d = w.push(0.0, rank(("a", 70)))  # exactly 0.70 is not > 0.70
    assert d.state == "unknown"


def test_window_expires_even_when_camera_stops_sending():
    w = PlaceWindow()
    w.push(0, rank(("a", 200)))
    assert w.decide(4).place == "a"
    assert w.decide(6).state == "holding"
    assert w.decide(11).place is None


def test_unverified_localizer_result_cannot_drive_agent(tmp_path):
    state = make_state(tmp_path)
    state.on_frame(0, {"ranking": rank(("a", 200))}, [], {"state":"UNCERTAIN", "pano_id":"a"})
    assert state.tool("get_location", {})["place"] == "unknown"


# ---- graph and guidance --------------------------------------------------------------
def test_wall_blocks_an_edge():
    walls = np.array([[1.0, -1.0, 1.0, 1.0]])
    assert not clear_line((0, 0), (2, 0), walls)
    assert clear_line((0, 0), (0, 2), walls)


def corridor(walls=None):
    pts = {"a": (0.0, 0.0), "b": (3.0, 0.0), "c": (6.0, 0.0), "d": (6.0, 3.0)}
    frames = {k: np.array([[1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]]) for k in pts}  # yaw 0 looks +y
    return EntranceMap(pts, {k: f"scan point {k}" for k in pts},
                       np.zeros((0, 4)) if walls is None else walls, frames, goal="d")


def test_route_follows_scan_points():
    m = corridor()
    assert shortest_path(m.graph, "a", "d")[0] == ["a", "b", "c", "d"]


def test_mesh_heading_and_clock():
    M = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]])
    assert mesh_heading_deg(M, 0) == pytest.approx(0)
    assert mesh_heading_deg(M, 90) == pytest.approx(90)
    assert clock(0) == 12 and clock(60) == 2 and clock(-90) == 9


def test_guide_turns_then_goes_straight_then_arrives():
    g = EntranceGuide(corridor())
    assert g.start("a")["ok"]
    # facing +y (yaw 0) while the next point is to the east (+x): turn right to 3 o'clock
    assert g.update(0.0, "a", "confident", 0.0, None) == "Turn right, to your 3 o'clock."
    assert g.update(0.5, "a", "confident", 0.0, None) is None  # same text, not repeated yet
    assert g.update(7.0, "a", "confident", 0.0, None) == "Turn right, to your 3 o'clock."  # 6 s reminder
    assert g.update(8.0, "a", "confident", 90.0, None) == "Go straight, about 3 meters."
    assert g.update(9.0, "c", "confident", 0.0, None) == "Go straight, about 3 meters."
    assert g.update(10.0, "d", "confident", 0.0, None) == "You're at the entrance."
    assert not g.active


def test_guide_pauses_once_when_place_unknown():
    g = EntranceGuide(corridor())
    g.start("a")
    assert g.update(0.0, None, "unknown", None, None).startswith("Paused")
    assert g.update(7.0, None, "unknown", None, None) is None


def test_guide_uses_tracked_position_near_the_place():
    g = EntranceGuide(corridor())
    g.start("a")
    text = g.update(0.0, "b", "confident", 90.0, {"x": 4.0, "y": 0.0})
    assert text == "Go straight, about 2 meters."


def test_real_floor_reaches_the_entrance():
    from pathlib import Path

    data = Path(__file__).resolve().parents[2] / "data"
    if not (data / "matterport" / "walls_floor1.json").is_file():
        pytest.skip("no Matterport data")
    m = EntranceMap.load(data, "n0ppizy9u8xcadypd2bawceyc")
    assert m.goal in m.points and not m.unreachable(m.goal)


# ---- cues ----------------------------------------------------------------------------
def loc(state="LOCKED", pano="p", label="Lobby"):
    return Location(pano, label, state, 0.9, 50, 0.0, 0.9, None)


def test_nav_cue_outranks_location_and_hazard_outranks_nav():
    from server.protocol import Detection

    e = CueEngine(min_gap_s=0.8)
    cue = e.update([], loc(), 0.0, nav_text="Turn left, to your 9 o'clock.")
    assert cue.text == "Turn left, to your 9 o'clock." and cue.priority == 1
    chair = [Detection("chair", 0.9, (0.3, 0.2, 0.9, 0.9))]
    for t in (2.0, 2.1):
        e.update(chair, loc(), t)
    cue = e.update(chair, loc(), 3.0, nav_text="Go straight, about 2 meters.")
    assert cue.priority == 0 and cue.interrupt
    assert e.update([], loc(), 4.0).text == "Go straight, about 2 meters."  # held, then spoken


# ---- scene memory --------------------------------------------------------------------
def test_scene_memory_keeps_recent_objects():
    m = SceneMemory(memory_s=60)
    m.update(0.0, [{"cls": "potted plant", "direction": "left", "distance": "close"}], "a")
    m.update(30.0, [], "a")
    s = m.summary(30.0)
    assert s["now"] == [] and s["recent"][0]["object"] == "plant" and s["recent"][0]["seconds_ago"] == 30
    m.update(100.0, [], "a")
    assert m.summary(100.0)["recent"] == []


# ---- agent ---------------------------------------------------------------------------
def test_parse_reply():
    assert parse_reply("HEARD: take me out\nREPLY: Starting guidance.") == ("take me out", "Starting guidance.")
    assert parse_reply("Okay.") == ("", "Okay.")


def make_state(tmp_path):
    s = AgentState(tmp_path, entrance="d", log_dir=tmp_path / "log")
    s.map = corridor()
    s.guide = EntranceGuide(s.map)
    s.place_label = s.map.label
    return s


class FakeModels:
    """First call asks for start_navigation, second returns the spoken reply."""

    def __init__(self):
        self.calls = []

    def generate_content(self, model, contents, config=None):
        self.calls.append(contents)
        if len(self.calls) == 1:
            fc = SimpleNamespace(name="start_navigation", args={"destination": "entrance"})
            return SimpleNamespace(function_calls=[fc], text=None,
                                   candidates=[SimpleNamespace(content=_model_content())])
        return SimpleNamespace(function_calls=None, text="HEARD: take me to the entrance\nREPLY: Okay, let's go.",
                               candidates=[])


def _model_content():
    from google.genai import types

    return types.Content(role="model", parts=[types.Part.from_text(text="calling")])


def test_agent_calls_tool_and_logs(tmp_path):
    state = make_state(tmp_path)
    state.on_frame(0.0, {"ranking": rank(("a", 200))}, [], {"pano_id": "a", "yaw_deg": 0.0})
    models = FakeModels()
    agent = GeminiAgent(state, model="fake", client=SimpleNamespace(models=models))
    out = agent.talk(b"RIFF....", "audio/wav")
    assert out["heard"] == "take me to the entrance" and out["reply"] == "Okay, let's go."
    assert out["tools"][0]["name"] == "start_navigation" and out["tools"][0]["result"]["ok"]
    assert state.guide.active
    # guidance now speaks on the next frame
    assert state.on_frame(1.0, {"ranking": rank(("a", 200))}, [], {"pano_id": "a", "yaw_deg": 0.0}) \
        == "Turn right, to your 3 o'clock."
    rows = [json.loads(x) for x in (tmp_path / "log" / f"{state.session}.jsonl").read_text().splitlines()]
    assert {r["kind"] for r in rows} == {"frame", "talk"}
    assert len(agent.history) == 2


def test_location_tool_never_guesses(tmp_path):
    state = make_state(tmp_path)
    assert state.tool("get_location", {})["place"] == "unknown"
    state.on_frame(0.0, {"ranking": rank(("d", 200))}, [], {"pano_id": "d", "yaw_deg": 0.0})
    out = state.tool("get_location", {})
    assert out["place"] == "the entrance" and out["is_entrance"]


def test_talk_endpoint_turns_errors_into_speech(tmp_path):
    from fastapi import FastAPI

    state = make_state(tmp_path)

    class Broken:
        def generate_content(self, **kw):
            raise RuntimeError("402 RESOURCE_EXHAUSTED")

    agent = GeminiAgent(state, model="fake", client=SimpleNamespace(models=Broken()))
    app = FastAPI()
    app.include_router(agent_router(lambda: agent))
    c = TestClient(app)
    r = c.post("/api/agent/talk", files={"audio": ("clip.m4a", b"not really audio", "audio/m4a")})
    assert r.status_code == 200 and "credit" in r.json()["reply"]
    assert c.get("/api/agent/state").json()["decision"]["state"] == "unknown"
