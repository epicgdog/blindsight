import json
import math
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from server.agent import AgentState, LiveAgent, agent_router
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


def test_window_decides_after_three_clear_wins_then_holds_then_unknown():
    w = PlaceWindow()
    for t in (0.0, 1.0):
        assert w.push(t, rank(("a", 125), ("b", 80))).state == "unknown"  # 2 wins: not yet
    d = w.push(2.0, rank(("a", 125), ("b", 80)))  # 125 >= 1.3 x 80: a steady winner decides
    assert (d.place, d.state, d.confident_frames) == ("a", "confident", 3)
    w.push(8.0, rank(("c", 20)))  # weak frames only; old wins left the 5 s window
    assert (w.decide(8.0).place, w.decide(8.0).state) == ("a", "holding")
    assert w.push(12.5, rank(("c", 20))).place is None


def test_window_needs_strength_and_margin():
    w = PlaceWindow()
    for t in range(5):
        d = w.push(float(t), rank(("a", 100), ("b", 90)))  # too close to the runner-up
    assert d.state == "unknown"
    w.reset()
    for t in range(5):
        d = w.push(float(t), rank(("a", 29)))  # below N_MIN
    assert d.state == "unknown"


def test_window_expires_even_when_camera_stops_sending():
    w = PlaceWindow()
    for t in (0, 0.5, 1):
        w.push(t, rank(("a", 200)))
    assert w.decide(4).place == "a"
    assert w.decide(7).state == "holding"
    assert w.decide(12).place is None


def test_only_verified_frames_drive_the_agent(tmp_path):
    state = make_state(tmp_path)
    for t in range(3):  # the server sends no match for unverified frames
        state.on_frame(t, None, [], {"state": "LOST", "pano_id": "a"})
    assert state.tool("get_location", {})["place"] == "unknown"
    for t in range(3, 6):
        state.on_frame(t, {"ranking": rank(("a", 200))}, [], {"state": "UNCERTAIN", "pano_id": "a"})
    assert state.decision.place == "a"


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


def test_guide_pauses_once_when_place_unknown():
    g = EntranceGuide(corridor())
    g.start("a")
    assert g.update(0.0, None, "unknown", None, None).startswith("Hold the phone steady")
    assert g.update(7.0, None, "unknown", None, None) is None


def test_real_floor_reaches_the_entrance():
    from pathlib import Path

    data = Path(__file__).resolve().parents[2] / "data"
    if not (data / "matterport" / "walls_floor1.json").is_file():
        pytest.skip("no Matterport data")
    m = EntranceMap.load(data, "n0ppizy9u8xcadypd2bawceyc")
    assert m.goal in m.points and not m.unreachable(m.goal)


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
def make_state(tmp_path):
    s = AgentState(tmp_path, entrance="d", log_dir=tmp_path / "log")
    s.map = corridor()
    s.guide = EntranceGuide(s.map)
    s.place_label = s.map.label
    return s


AT_A = ({"ranking": rank(("a", 200))}, [], {"pano_id": "a", "yaw_deg": 0.0})


def test_frames_emit_guidance_and_close_obstacles(tmp_path):
    state = make_state(tmp_path)
    said = []
    state.emit = lambda text, urgent: said.append((text, urgent))
    state.guide.start("a")
    state.on_frame(0.0, *AT_A)
    chair = [{"cls": "chair", "direction": "ahead", "distance": "very close"}]
    state.on_frame(0.5, AT_A[0], chair, AT_A[2])
    state.on_frame(1.0, AT_A[0], chair, AT_A[2])  # second frame in a row: urgent event
    state.on_frame(1.5, AT_A[0], chair, AT_A[2])  # not repeated within 6 s
    assert said == [("[step] Turn right, to your 3 o'clock.", False), ("[urgent] Stop. chair very close, ahead.", True)]
    state.quiet = True
    state.guide.last_text = None  # would speak again, but the user is talking
    assert state.on_frame(2.0, *AT_A) == []


def test_location_tool_never_guesses(tmp_path):
    state = make_state(tmp_path)
    assert state.tool("get_location", {})["place"] == "unknown"
    state.on_frame(0.0, {"ranking": rank(("d", 200))}, [], {"pano_id": "d", "yaw_deg": 0.0})
    out = state.tool("get_location", {})
    assert out["place"] == "the entrance" and out["is_entrance"]


# ---- Live agent with a fake session ----------------------------------------------------
def sc(**kw):
    base = dict(input_transcription=None, output_transcription=None, model_turn=None, turn_complete=False)
    base.update(kw)
    return SimpleNamespace(server_content=SimpleNamespace(**base), tool_call=None)


def audio_turn(text):
    part = SimpleNamespace(inline_data=SimpleNamespace(data=b"\x00\x01" * 2400))
    return [sc(output_transcription=SimpleNamespace(text=text), model_turn=SimpleNamespace(parts=[part])),
            sc(turn_complete=True)]


class FakeSession:
    """Answers a talk turn with a start_navigation call then a spoken reply; events with speech."""

    def __init__(self):
        import asyncio

        self.q = asyncio.Queue()
        self.sent = []

    async def send_realtime_input(self, **kw):
        self.sent.append(("realtime", sorted(kw)))
        if "activity_end" in kw:
            fc = SimpleNamespace(id="1", name="start_navigation", args={"destination": "entrance"})
            await self.q.put([sc(input_transcription=SimpleNamespace(text="take me to the entrance")),
                              SimpleNamespace(server_content=None, tool_call=SimpleNamespace(function_calls=[fc])),
                              sc(turn_complete=True)])  # real Live ends the tool-call turn silently

    async def send_tool_response(self, function_responses):
        self.sent.append(("tool", [(r.name, r.response) for r in function_responses]))
        await self.q.put(audio_turn("Guiding you to the entrance, about 3 meters."))

    async def send_client_content(self, turns, turn_complete):
        text = turns["parts"][0]["text"]
        self.sent.append(("context" if text.startswith("CONTEXT:") else "event", text))
        if turn_complete:
            await self.q.put(audio_turn("Turn right, to your 3 o'clock."))

    async def receive(self):
        for r in await self.q.get():
            yield r


class FakeConnect:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *a):
        return False


def fake_client(session):
    return SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=lambda model, config: FakeConnect(session))))


def test_live_agent_talk_calls_tools_and_speaks(tmp_path):
    import asyncio

    state = make_state(tmp_path)
    state.on_frame(0.0, *AT_A)
    session = FakeSession()
    agent = LiveAgent(state, client=fake_client(session))

    async def run():
        out = await agent.talk(b"\x00" * 32000)
        await agent.event("Turn right, to your 3 o'clock.")
        for _ in range(50):
            if sum(m["last"] for m in agent.outbox) >= 2:
                break
            await asyncio.sleep(0.01)
        await agent.close()
        return out

    out = asyncio.run(run())
    assert out["heard"] == "take me to the entrance"
    assert out["reply"].startswith("Guiding you") and out["tools"][0]["name"] == "start_navigation"
    assert out["tools"][0]["result"]["ok"] and state.guide.active
    assert agent.clips[out["clip"]][:4] == b"RIFF"
    assert out["turn"] and any(message["turn"] == out["turn"] for message in agent.outbox)
    assert [x[0] for x in session.sent[:4]] == ["context", "realtime", "realtime", "realtime"]  # ctx, start, audio, end
    assert ("event", "EVENT Turn right, to your 3 o'clock.") in session.sent
    segs = agent.take_outbox()  # no phone attached: segments wait in the outbox
    firsts = [(m["type"], m["interrupt"]) for m in segs if m["first"]]
    assert firsts == [("voice", True), ("voice", False)]  # the answer cuts in, a guidance event queues
    assert sum(m["last"] for m in segs) == 2 and not any(m["interrupt"] for m in segs if not m["first"])
    assert all(m["url"].startswith("/api/agent/clip/") for m in segs if "url" in m)
    rows = [json.loads(x) for x in (tmp_path / "log" / f"{state.session}.jsonl").read_text().splitlines()]
    assert {r["kind"] for r in rows} == {"frame", "talk", "event"}


def test_talk_endpoint_reports_errors(tmp_path):
    from fastapi import FastAPI

    state = make_state(tmp_path)

    class Broken:
        def connect(self, model, config):
            raise RuntimeError("402 RESOURCE_EXHAUSTED")

    agent = LiveAgent(state, client=SimpleNamespace(aio=SimpleNamespace(live=Broken())))
    app = FastAPI()
    app.include_router(agent_router(lambda: agent))
    c = TestClient(app)
    from server.voice import pcm_to_wav
    wav = pcm_to_wav(bytes(16000), 16000)
    r = c.post("/api/agent/talk", files={"audio": ("clip.wav", wav, "audio/wav")})
    assert r.status_code == 200 and r.json()["detail"] == "out of credit" and not r.json().get("url")
    assert c.post("/api/agent/talk", files={"audio": ("clip.m4a", b"junk", "audio/m4a")}).status_code == 422
    assert c.get("/api/agent/state").json()["decision"]["state"] == "unknown"
    assert c.get("/api/agent/clip/nope").status_code == 404


def test_nav_endpoint_turns_guidance_off_and_on_from_the_decided_place(tmp_path):
    from fastapi import FastAPI

    state = make_state(tmp_path)
    for t in (0.0, 0.5, 1.0):  # three clear wins: the place is decided as "a"
        state.on_frame(t, *AT_A)
    agent = LiveAgent(state, client=SimpleNamespace())
    app = FastAPI()
    app.include_router(agent_router(lambda: agent))
    c = TestClient(app)
    off = c.post("/api/agent/nav?on=0").json()
    assert off["ok"] and not off["active"] and not state.guide.active and state.guide.status == {"active": False}
    on = c.post("/api/agent/nav?on=1").json()
    assert on["ok"] and on["active"] and state.guide.active
    assert on["distance_m"] == pytest.approx(9.0)  # planned at once from a: a-b-c-d
    assert c.post("/api/agent/nav?on=0").json()["active"] is False


def test_wav_response_serves_byte_ranges_for_ios():
    from server.voice import wav_response
    data = bytes(range(10))
    full = wav_response(data, None)
    assert full.status_code == 200 and full.body == data and full.headers["accept-ranges"] == "bytes"
    probe = wav_response(data, "bytes=0-1")
    assert probe.status_code == 206 and probe.body == data[:2]
    assert probe.headers["content-range"] == "bytes 0-1/10"
    assert wav_response(data, "bytes=4-").body == data[4:]
    assert wav_response(data, "bytes=-3").body == data[7:]
    assert wav_response(data, "bytes=8-100").headers["content-range"] == "bytes 8-9/10"
    assert wav_response(data, "bytes=20-").status_code == 416


def test_talk_endpoint_accepts_raw_body(tmp_path):
    from fastapi import FastAPI

    state = make_state(tmp_path)

    class Broken:
        def connect(self, model, config):
            raise RuntimeError("402 RESOURCE_EXHAUSTED")

    agent = LiveAgent(state, client=SimpleNamespace(aio=SimpleNamespace(live=Broken())))
    app = FastAPI()
    app.include_router(agent_router(lambda: agent))
    c = TestClient(app)
    from server.voice import pcm_to_wav
    wav = pcm_to_wav(bytes(16000), 16000)
    r = c.post("/api/agent/talk", content=wav, headers={"Content-Type": "audio/m4a"})
    assert r.status_code == 200 and r.json()["detail"] == "out of credit"  # decoded and reached the agent
    assert c.post("/api/agent/talk", content=b"", headers={"Content-Type": "audio/m4a"}).status_code == 422
