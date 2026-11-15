import base64
import io
import json
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
        assert client.get("/healthz").json()["voice_agent_api_version"] == 2
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
    import threading

    entered, release, completed = threading.Event(), threading.Event(), threading.Event()

    class SlowDetector:
        def detect(self, frame):
            entered.set()
            release.wait(3)
            completed.set()
            return []

    with TestClient(create_app(settings, detector=SlowDetector())) as client:
        with client.websocket_connect("/ws/phone") as phone:
            phone.send_bytes(packet(0))
            try:
                assert entered.wait(2)
                for seq in range(1, 31):
                    phone.send_bytes(packet(seq))
                assert client.get("/healthz").status_code == 200
                assert not completed.is_set(), "Health request waited for blocked inference"
            finally:
                release.set()
            seqs = []
            while not seqs or seqs[-1] != 30:
                seqs.append(phone.receive_json()["seq"])
            assert len(seqs) <= 3
            assert client.get("/metrics").json()["dropped"] >= 28


@pytest.mark.timeout(30)
def test_bad_packets_and_reconnect(settings, packet):
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws/phone") as phone:
            phone.send_bytes(b"bad")  # a broken frame closes the socket and counts as invalid
            with pytest.raises(WebSocketDisconnect):
                phone.receive_json()
        for seq in range(10):
            with client.websocket_connect("/ws/phone") as phone:
                phone.send_bytes(packet(seq))
                assert phone.receive_json()["seq"] == seq
        assert client.get("/metrics").json()["invalid"] == 1


@pytest.mark.timeout(30)
def test_text_messages_are_phone_sensor_samples_and_never_close_the_socket(settings, packet):
    """Text is the phone's IMU stream. Garbage text is dropped; the connection keeps working."""
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws/phone") as phone:
            phone.send_text("text")
            phone.send_text('{"type": "imu", "heading_deg": 90, "moving": false, "steps": 3}')
            phone.send_text('{"type": "imu", "heading_deg": "north"}')
            phone.send_bytes(packet(5))
            assert phone.receive_json()["seq"] == 5
        assert client.get("/metrics").json()["invalid"] == 0


def test_only_one_phone_and_fresh_state_on_reconnect(settings, packet):
    with TestClient(create_app(settings, localizer_factory=VerifiedLocalizer)) as client:
        for _ in range(2):
            with client.websocket_connect("/ws/phone") as phone:
                phone.send_bytes(packet())
                assert phone.receive_json()["cue"]["text"] == "View matches the test lobby."
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


def test_part3_metadata_references_and_metrics(settings):
    root = settings.data_dir / "pano" / "chosen"
    (root / "refs").mkdir(parents=True)
    (settings.data_dir / "pano/pano.json").write_text(
        '{"pano_id":"chosen","label":"Demo lobby","model_id":"model"}'
    )
    # Multiple panoramas must use Part 3's chosen pano, not directory ordering.
    (settings.data_dir / "pano/other").mkdir()
    Image.new("RGB", (10, 10)).save(root / "face_0.jpg")
    Image.new("RGB", (10, 10)).save(root / "refs/y000.jpg")
    (root / "refs/refs.json").write_text(
        '{"refs":[{"name":"y000","file":"y000.jpg","yaw":0,"pitch":0}]}'
    )
    (settings.data_dir / "eval").mkdir()
    (settings.data_dir / "eval/results.json").write_text('{"simulated":true,"targets":[]}')
    with TestClient(create_app(settings)) as client:
        pano = client.get("/api/pano").json()
        assert pano["pano_id"] == "chosen" and pano["label"] == "Demo lobby"
        assert client.get(pano["refs"][0]["path"]).status_code == 200
        assert client.get("/metrics").json()["localization_accuracy"]["simulated"] is True


def test_resettable_localizer_is_reused_and_negatives_added(settings, packet):
    class Resettable(VerifiedLocalizer):
        resets = negatives = 0

        def reset(self):
            self.resets += 1

        def add_negative(self, frame):
            self.negatives += 1

    instance = Resettable()
    built = []

    def factory():
        built.append(instance)
        return instance

    with TestClient(create_app(settings, localizer_factory=factory)) as client:
        for _ in range(2):
            with client.websocket_connect("/ws/phone") as phone:
                phone.send_bytes(packet())
                assert phone.receive_json()["cue"]
                assert client.post("/api/capture/neg").status_code == 200
    assert len(built) == 1 and instance.resets == 2 and instance.negatives == 2


def test_oversized_frame_is_logged_with_its_dimensions(settings, caplog):
    """A phone sending full-resolution photos must be diagnosable from the server log."""
    import logging

    from server.config import Settings
    from server.protocol import FrameHeader, ProtocolError, decode_frame, encode_frame
    from server.server import decode_image

    small = Settings(
        data_dir=settings.data_dir,
        detector="stub",
        localizer="stub",
        viewer_dir=settings.viewer_dir,
        max_image_pixels=1000,
    )
    buffer = io.BytesIO()
    Image.new("RGB", (60, 80), (1, 2, 3)).save(buffer, format="JPEG")
    packet = decode_frame(encode_frame(FrameHeader(1, 1.0, None, 60, 80), buffer.getvalue()))
    with caplog.at_level(logging.WARNING, logger="blindsight"):
        with pytest.raises(ProtocolError):
            decode_image(packet, small)
    assert any("60x80" in r.getMessage() and "1000" in r.getMessage() for r in caplog.records)


def test_full_resolution_phone_photo_is_downscaled_not_rejected(settings):
    """An iPhone 12 MP photo (4032x3024) must flow through at working resolution."""
    from server.protocol import FrameHeader, decode_frame, encode_frame
    from server.server import decode_image

    buffer = io.BytesIO()
    Image.new("RGB", (4032, 3024), (40, 90, 160)).save(buffer, format="JPEG", quality=30)
    packet = decode_frame(encode_frame(FrameHeader(1, 1.0, None, 4032, 3024), buffer.getvalue()))
    rgb, jpeg = decode_image(packet, settings)
    assert max(rgb.shape[:2]) <= settings.max_frame_side
    assert rgb.shape[1] / rgb.shape[0] == pytest.approx(4032 / 3024, abs=0.01)  # aspect kept
    with Image.open(io.BytesIO(jpeg)) as out:
        assert out.size == (rgb.shape[1], rgb.shape[0])


def test_absurdly_large_frames_are_still_rejected(settings):
    from dataclasses import replace

    from server.protocol import FrameHeader, ProtocolError, decode_frame, encode_frame
    from server.server import decode_image

    buffer = io.BytesIO()
    Image.new("RGB", (2000, 2000), (1, 1, 1)).save(buffer, format="JPEG")
    packet = decode_frame(encode_frame(FrameHeader(1, 1.0, None, 2000, 2000), buffer.getvalue()))
    with pytest.raises(ProtocolError):
        decode_image(packet, replace(settings, max_image_pixels=1_000_000))
