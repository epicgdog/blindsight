"""Rendered-room integration; real matching/HTTP, a simulated Gemini Live session."""

import io
import sys
import time
import wave
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p3_room import Room, pos3, write_dataset
from test_agent import FakeSession, fake_client

from server.agent import LiveAgent
from server.config import Settings
from server.entrance_nav import EntranceGuide, EntranceMap
from server.geometry import SiftMatcher
from server.localize import ClassicEmbedder
from server.localize import Settings as MatchSettings
from server.property_localize import Localizer
from server.protocol import FrameHeader, encode_frame
from server.server import create_app


def wav_clip():
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(np.zeros(16000, dtype=np.int16).tobytes())
    return data.getvalue()


def test_rendered_camera_to_location_voice_tools_and_guidance_events(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT", "1")  # conftest turns the agent off; this test needs it
    room = Room()
    ids = ["room_p0", "room_p1", "room_p3"]
    root = write_dataset(tmp_path, room, S=384, ids=ids)
    localizer = Localizer(
        root,
        matcher=SiftMatcher(),
        embedder=ClassicEmbedder(),
        settings=MatchSettings.load(calibration={}, use_config=False, use_env=False),
        verbose=False,
    )
    settings = Settings(data_dir=root, detector="stub", localizer="real")
    app = create_app(settings, localizer_factory=lambda: localizer)
    with TestClient(app) as client:
        runtime = app.state.runtime
        state = runtime.agent.state
        # Known synthetic open-room graph. This is NOT a surveyed Rabinowitz route.
        points = {p: (float(pos3(p)[0]), float(pos3(p)[2])) for p in ids}
        axes = {p: np.array([[1.0, 0, 0], [0, 0, 1.0], [0, 1.0, 0]]) for p in ids}
        state.map = EntranceMap(points, {p: p for p in ids}, np.zeros((0, 4)), axes, goal="room_p3")
        state.guide = EntranceGuide(state.map)
        state.place_label = state.map.label
        session = FakeSession()  # only language is simulated; the tools read the live pipeline
        runtime.agent = LiveAgent(state, client=fake_client(session))
        with (
            client.websocket_connect("/ws/viewer") as viewer,
            client.websocket_connect("/ws/phone") as phone,
        ):
            seq = 0

            def stream(place):
                nonlocal seq
                image = room.render(pos3(place), 30, 0, 0, (360, 480), 55)
                jpeg = io.BytesIO()
                Image.fromarray(image).save(jpeg, "JPEG", quality=90)
                phone.send_bytes(
                    encode_frame(FrameHeader(seq, seq * 100.0, None, 360, 480), jpeg.getvalue())
                )
                seq += 1
                return phone.receive_json()

            for _ in range(4):
                result = stream("room_p0")
            assert result["location"]["state"] == "LOCKED"
            assert result["location"]["pano_id"] == "room_p0"
            assert client.get("/api/agent/state").json()["decision"]["place"] == "room_p0"
            reply = client.post(
                "/api/agent/talk", files={"audio": ("sim.wav", wav_clip(), "audio/wav")}
            )
            assert reply.status_code == 200
            assert reply.json()["tools"][0]["result"]["ok"]
            assert reply.json()["reply"].startswith("Guiding you")
            assert state.guide.active
            # The Live agent is the only voice: frames carry no spoken cue, and guidance reaches the
            # model as events it words itself.
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(k == "event" for k, _ in session.sent):
                result = stream("room_p0")
                assert result.get("cue") is None
                time.sleep(0.05)
            assert any(k == "event" and v.startswith("EVENT ") for k, v in session.sent), session.sent
            assert viewer.receive_json()["jpeg_b64"]
            for _ in range(4):
                result = stream("room_p1")
            assert result["location"]["pano_id"] == "room_p1"
            state._received_at -= 11
            assert state.tool("get_location", {})["place"] == "unknown"
