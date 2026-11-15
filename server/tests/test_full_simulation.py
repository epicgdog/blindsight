"""Rendered-room integration; real matching/HTTP, simulated Gemini and audio playback."""

import io
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p3_room import Room, pos3, write_dataset

from server.agent import GeminiAgent
from server.config import Settings
from server.cues import speech_seconds
from server.entrance_nav import EntranceGuide, EntranceMap
from server.geometry import SiftMatcher
from server.localize import ClassicEmbedder
from server.localize import Settings as MatchSettings
from server.property_localize import Localizer
from server.protocol import FrameHeader, encode_frame
from server.server import create_app


class SimulatedGemini:
    """Only language interpretation is mocked; real tools read the live pipeline."""

    def __init__(self):
        self.turn = 0

    def generate_content(self, **kwargs):
        from google.genai import types

        self.turn += 1
        if self.turn % 2:
            return SimpleNamespace(
                function_calls=[
                    SimpleNamespace(name="start_navigation", args={"destination": "entrance"})
                ],
                text=None,
                candidates=[
                    SimpleNamespace(
                        content=types.Content(
                            role="model",
                            parts=[
                                types.Part.from_function_call(
                                    name="start_navigation", args={"destination": "entrance"}
                                )
                            ],
                        )
                    )
                ],
            )
        return SimpleNamespace(
            function_calls=[], text="HEARD: Take me to the entrance\nREPLY: Starting guidance."
        )


def wav_clip():
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(np.zeros(16000, dtype=np.int16).tobytes())
    return data.getvalue()


def test_rendered_camera_to_location_voice_tools_guidance_and_ack(tmp_path):
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
        runtime.agent = GeminiAgent(state, client=SimpleNamespace(models=SimulatedGemini()))
        with (
            client.websocket_connect("/ws/viewer") as viewer,
            client.websocket_connect("/ws/phone") as phone,
        ):
            seq = 0
            quiet_until = 0.0

            def stream(place):
                nonlocal seq, quiet_until
                image = room.render(pos3(place), 30, 0, 0, (360, 480), 55)
                jpeg = io.BytesIO()
                Image.fromarray(image).save(jpeg, "JPEG", quality=90)
                phone.send_bytes(
                    encode_frame(FrameHeader(seq, seq * 100.0, None, 360, 480), jpeg.getvalue())
                )
                seq += 1
                result = phone.receive_json()
                if result.get("cue"):
                    quiet_until = time.monotonic() + max(
                        settings.min_gap_s, speech_seconds(result["cue"]["text"])
                    )
                return result

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
            assert reply.json()["reply"] == "Starting guidance."
            assert state.guide.active
            # Model playback of the reply; guidance must honor the normal speech gap.
            time.sleep(max(0, quiet_until - time.monotonic()) + 0.05)
            result = stream("room_p0")
            assert result["cue"] is not None
            # Simulated speaker acknowledgment verifies telemetry, not acoustic output.
            assert (
                client.post(
                    "/api/speech", json={"cue_id": result["cue"]["id"], "played": True}
                ).status_code
                == 200
            )
            assert client.get("/metrics").json()["speech_acknowledged"]
            assert viewer.receive_json()["jpeg_b64"]
            for _ in range(4):
                result = stream("room_p1")
            assert result["location"]["pano_id"] == "room_p1"
            state._received_at -= 11
            assert state.tool("get_location", {})["place"] == "unknown"
