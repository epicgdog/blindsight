import base64
import io
import json
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from server.localize_stub import Localizer
from server.protocol import Location
from server.record import replay_frames
from server.server import create_app


class VerifiedLocalizer:
    def __init__(self):
        self.attempted = 0

    def update(self, frame_rgb, detections, heading_deg):
        return Location("pano-1", "the test lobby", "LOCKED", 0.9, 40, 12, 0.8, "ref-1", 0.85)

    def add_reference(self, frame_rgb):
        self.attempted += 1
        return self.attempted % 2 == 0


def test_http_and_websocket_contract(settings, packet):
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        assert client.get("/").status_code == 200
        assert client.post("/api/capture/pos").status_code == 409
        with client.websocket_connect("/ws/viewer") as viewer:
            with client.websocket_connect("/ws/phone") as phone:
                phone.send_bytes(packet(7))
                result = phone.receive_json()
                displayed = viewer.receive_json()
                assert result["seq"] == displayed["seq"] == 7
                assert result["location"]["state"] == "UNCERTAIN"
                assert result["cue"] is None
                assert set(result["server_ms"]) == {"decode", "yolo", "embed", "total"}
                assert set(result["location"]) == {
                    "pano_id",
                    "label",
                    "score",
                    "ema",
                    "best_ref",
                    "state",
                    "inliers",
                    "yaw_deg",
                    "yaw_conf",
                }
                with Image.open(io.BytesIO(base64.b64decode(displayed["jpeg_b64"]))) as frame:
                    assert frame.size == (48, 64)
                assert client.post("/api/capture/pos").status_code == 200
                assert client.post("/api/capture/neg").status_code == 200
                assert client.post("/api/capture/else").status_code == 404
                assert client.get("/metrics").json()["processed"] == 1
        assert len(list((settings.data_dir / "onsite/pos").glob("*.jpg"))) == 1
        assert client.get("/data/onsite/pos").status_code == 404


def test_warmup_keeps_verified_only(settings, packet):
    app = create_app(settings, localizer_factory=VerifiedLocalizer)
    with TestClient(app) as client:
        assert client.post("/api/capture/auto").status_code == 409
        with client.websocket_connect("/ws/phone") as phone:
            phone.send_bytes(packet())
            phone.receive_json()
            assert client.post("/api/capture/auto?seconds=20").status_code == 200
            assert client.post("/api/capture/auto?seconds=20").status_code == 409
            for seq in range(4):
                phone.send_bytes(packet(seq + 1))
                phone.receive_json()
            status = client.get("/api/capture/auto").json()
            assert status["attempted"] == 4 and status["accepted"] == 2
            assert status["running"] and status["seconds"] == 20 and status["kept"] == 2
            assert 0 <= status["elapsed"] <= 20
            assert len(list((settings.data_dir / "onsite/pos").glob("*.jpg"))) == 2


def test_stub_never_verifies(settings, packet):
    assert Localizer().add_reference(None) is False
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws/phone") as phone:
            phone.send_bytes(packet())
            phone.receive_json()
            client.post("/api/capture/auto")
            phone.send_bytes(packet(1))
            phone.receive_json()
            assert client.get("/api/capture/auto").json()["accepted"] == 0


def test_latest_wins_and_event_loop_remains_responsive(settings, packet):
    class SlowDetector:
        def detect(self, frame):
            time.sleep(0.1)
            return []

    with TestClient(create_app(settings, detector=SlowDetector())) as client:
        with client.websocket_connect("/ws/phone") as phone:
            phone.send_bytes(packet(0))
            time.sleep(0.02)
            for seq in range(1, 31):
                phone.send_bytes(packet(seq))
            start = time.monotonic()
            assert client.get("/healthz").status_code == 200
            assert time.monotonic() - start < 0.08
            seqs = []
            while not seqs or seqs[-1] != 30:
                seqs.append(phone.receive_json()["seq"])
            assert len(seqs) <= 3
            assert client.get("/metrics").json()["dropped"] >= 28


def test_bad_packets_and_reconnect(settings, packet):
    with TestClient(create_app(settings)) as client:
        for bad in (b"bad", "text"):
            with client.websocket_connect("/ws/phone") as phone:
                phone.send_bytes(bad) if isinstance(bad, bytes) else phone.send_text(bad)
                with pytest.raises(WebSocketDisconnect):
                    phone.receive_json()
        for seq in range(10):
            with client.websocket_connect("/ws/phone") as phone:
                phone.send_bytes(packet(seq))
                assert phone.receive_json()["seq"] == seq
        assert client.get("/metrics").json()["invalid"] == 2


def test_only_one_phone_and_fresh_state_on_reconnect(settings, packet):
    with TestClient(create_app(settings, localizer_factory=VerifiedLocalizer)) as client:
        for _ in range(2):
            with client.websocket_connect("/ws/phone") as phone:
                phone.send_bytes(packet())
                assert phone.receive_json()["cue"]["text"] == "You are in the test lobby"
                with client.websocket_connect("/ws/phone") as other:
                    with pytest.raises(WebSocketDisconnect) as error:
                        other.receive_json()
                    assert error.value.code == 1013


def test_record_replay_through_pipeline(settings, packet):
    settings = replace(settings, record=True)
    with TestClient(create_app(settings, localizer_factory=VerifiedLocalizer)) as client:
        with client.websocket_connect("/ws/phone") as phone:
            for seq in range(3):
                phone.send_bytes(packet(seq))
                phone.receive_json()
    session = next((settings.data_dir / "sessions").iterdir())
    frames = list(replay_frames(session))
    assert len(frames) == 3 and frames[0][0] == 0
    rows = [json.loads(line) for line in (session / "frames.jsonl").read_text().splitlines()]
    assert sum(row["result"]["cue"] is not None for row in rows) == 1
    from server.protocol import encode_frame

    with TestClient(
        create_app(replace(settings, record=False), localizer_factory=VerifiedLocalizer)
    ) as client:
        with client.websocket_connect("/ws/phone") as phone:
            cues = []
            for _, frame in frames:
                phone.send_bytes(encode_frame(frame.header, frame.jpeg))
                cues.append(phone.receive_json()["cue"])
            assert sum(cue is not None for cue in cues) == 1


def test_speech_evidence_requires_real_cue_and_is_deduplicated(settings, packet):
    with TestClient(create_app(settings, localizer_factory=VerifiedLocalizer)) as client:
        assert client.get("/metrics").json()["capture_to_audible_ms"]["p50"] is None
        assert client.post("/api/speech", json={"played": True}).status_code == 200
        with client.websocket_connect("/ws/phone") as phone:
            phone.send_bytes(packet())
            cue = phone.receive_json()["cue"]
            report = {"cue_id": cue["id"], "played": True, "capture_to_audible_ms": 200}
            assert client.post("/api/speech", json=report).status_code == 200
            assert client.post("/api/speech", json=report).status_code == 409
            assert client.get("/metrics").json()["capture_to_audible_ms"]["p95"] == 200


def test_viewer_assets_and_pano_metadata(settings):
    settings.viewer_dir.mkdir()
    (settings.viewer_dir / "index.html").write_text("<h1>viewer</h1>")
    (settings.viewer_dir / "app.js").write_text("const test = 1;")
    root = settings.data_dir / "pano" / "test-id"
    root.mkdir(parents=True)
    for i in range(6):
        Image.new("RGB", (10, 10)).save(root / f"face_{i}.jpg")
    (settings.data_dir / "calibration.json").write_text('{"T_HI": 0.8, "T_LO": 0.6}')
    with TestClient(create_app(settings)) as client:
        assert client.get("/").text == "<h1>viewer</h1>"
        assert client.get("/app.js").status_code == 200
        assert client.get("/viewer/app.js").status_code == 200
        pano = client.get("/api/pano").json()
        assert pano["pano_id"] == "test-id" and len(pano["faces"]) == 6
        assert all(client.get(url).status_code == 200 for url in pano["faces"])
