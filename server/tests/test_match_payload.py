"""The server forwards the localizer's ranking and footprint to phone and viewer."""

from fastapi.testclient import TestClient

from server.protocol import Location
from server.server import create_app

MATCH = {
    "ranking": [
        {"place": "pano-1", "sim": 0.91, "ref": 3, "inliers": 52},
        {"place": "pano-2", "sim": 0.55, "ref": 7, "inliers": 4},
    ],
    "footprint": [[0.2, 0.4], [0.3, 0.4], [0.3, 0.6], [0.2, 0.6]],
}


class RankedLocalizer:
    last_debug: dict = {}

    def update(self, frame_rgb, detections, heading_deg):
        type(self).last_debug = {"match": MATCH}
        return Location("pano-1", "lobby", "LOCKED", 0.9, 52, 12, 0.8, "ref-3", 0.85)

    def add_reference(self, frame_rgb):
        return False


class QuietLocalizer(RankedLocalizer):
    def update(self, frame_rgb, detections, heading_deg):
        type(self).last_debug = {"embed": 1.0}
        return Location("pano-1", "lobby", "UNCERTAIN", 0.1, 0, None, 0.0, None, 0.1)


def test_result_carries_match_for_phone_and_viewer(settings, packet):
    with TestClient(create_app(settings, localizer_factory=RankedLocalizer)) as client:
        with client.websocket_connect("/ws/viewer") as viewer:
            with client.websocket_connect("/ws/phone") as phone:
                phone.send_bytes(packet(1))
                result = phone.receive_json()
                shown = viewer.receive_json()
    assert result["match"] == MATCH
    assert shown["match"] == MATCH
    assert "jpeg_b64" in shown


def test_match_is_null_when_localizer_has_no_ranking(settings, packet):
    with TestClient(create_app(settings, localizer_factory=QuietLocalizer)) as client:
        with client.websocket_connect("/ws/phone") as phone:
            phone.send_bytes(packet(1))
            assert phone.receive_json()["match"] is None


def test_stub_localizer_reports_no_match(settings, packet):
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/ws/phone") as phone:
            phone.send_bytes(packet(1))
            assert phone.receive_json()["match"] is None


def test_pano_metadata_lists_place_names_for_the_viewer(tmp_path):
    import json

    from server.config import Settings
    from server.server import pano_metadata

    (tmp_path / "pano").mkdir()
    (tmp_path / "pano" / "pano.json").write_text(
        json.dumps(
            {
                "pano_id": "p0",
                "label": "the hallway",
                "scan_label": "0",
                "room": "Hallway",
                "negatives": [{"pano_id": "p1", "label": "34", "room": "Other / Hallway"}],
            }
        )
    )
    meta = pano_metadata(Settings(data_dir=tmp_path, detector="stub", localizer="stub"))
    assert meta["places"]["p0"] == {"room": "Hallway", "label": "0"}
    assert meta["places"]["p1"] == {"room": "Other / Hallway", "label": "34"}
