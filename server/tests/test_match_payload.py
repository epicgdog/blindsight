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


# ---- reference-view masking wiring ----------------------------------------------------


def test_masking_is_off_by_default_and_can_be_enabled(monkeypatch):
    import importlib

    import server.config as config

    monkeypatch.delenv("MASK_REFS", raising=False)
    assert importlib.reload(config).Settings().mask_refs is False
    monkeypatch.setenv("MASK_REFS", "1")
    assert importlib.reload(config).Settings().mask_refs is True
    monkeypatch.delenv("MASK_REFS")
    importlib.reload(config)


def _capture_localizer(monkeypatch):
    import importlib
    from types import SimpleNamespace

    seen = {}

    class FakeLocalizer:
        def __init__(
            self, data_dir=None, pano_id=None, ref_detector=None, ref_detector_id=None, places=None
        ):
            seen.update(ref_detector=ref_detector, ref_detector_id=ref_detector_id, places=places)

    monkeypatch.setattr(
        importlib, "import_module", lambda name: SimpleNamespace(Localizer=FakeLocalizer)
    )
    return seen


def test_real_detector_is_handed_to_the_localizer_for_masking(settings, monkeypatch):
    from dataclasses import replace

    from server.server import make_localizer

    seen = _capture_localizer(monkeypatch)

    class RealDetector:
        def detect(self, rgb):
            return []

    detector = RealDetector()
    make_localizer(replace(settings, localizer="real", mask_refs=True), detector)
    assert seen["ref_detector"] == detector.detect
    assert "yolo" in seen["ref_detector_id"]


def test_no_masking_with_stub_detector_or_when_disabled(settings, monkeypatch):
    from dataclasses import replace

    from server.detect import StubDetector
    from server.server import make_localizer

    seen = _capture_localizer(monkeypatch)
    make_localizer(replace(settings, localizer="real", mask_refs=True), StubDetector())
    assert seen["ref_detector"] is None
    make_localizer(replace(settings, localizer="real", mask_refs=False), object())
    assert seen["ref_detector"] is None


class MaskedLocalizer(RankedLocalizer):
    def masked_regions(self):
        return [[[0.1, 0.4], [0.2, 0.4], [0.2, 0.6], [0.1, 0.6]]]


def test_api_pano_lists_masked_regions(settings):
    with TestClient(create_app(settings, localizer_factory=MaskedLocalizer)) as client:
        regions = client.get("/api/pano").json()["masked_regions"]
    assert regions == [[[0.1, 0.4], [0.2, 0.4], [0.2, 0.6], [0.1, 0.6]]]


def test_api_pano_masked_regions_empty_without_support(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/pano").json()["masked_regions"] == []


# ---- candidate places and the minimap ----------------------------------------------------


def test_candidate_places_default_to_all_and_are_configurable(monkeypatch):
    import importlib

    import server.config as config

    monkeypatch.delenv("CANDIDATE_PLACES", raising=False)
    assert importlib.reload(config).Settings().candidate_places == "all"
    monkeypatch.setenv("CANDIDATE_PLACES", "12")
    assert importlib.reload(config).Settings().candidate_places == "12"
    monkeypatch.delenv("CANDIDATE_PLACES")
    importlib.reload(config)


def test_candidate_places_are_handed_to_the_localizer(settings, monkeypatch):
    from dataclasses import replace

    from server.server import make_localizer

    seen = _capture_localizer(monkeypatch)
    make_localizer(replace(settings, localizer="real", candidate_places="all"))
    assert seen["places"] == "all"
    make_localizer(replace(settings, localizer="real", candidate_places="1"))
    assert seen["places"] is None  # one place = the old single-pano behaviour


def test_api_pano_includes_the_minimap_data(settings):
    import json

    (settings.data_dir / "matterport").mkdir()
    (settings.data_dir / "matterport" / "panoLocations.json").write_text(
        json.dumps(
            {"panoLocations": [{"id": "a", "label": "0", "position": {"x": 1, "y": 2, "z": 1.3}}]}
        )
    )
    with TestClient(create_app(settings)) as client:
        pano = client.get("/api/pano").json()
    assert pano["map"]["points"][0]["id"] == "a"
    assert pano["map"]["floors"][0]["count"] == 1
    assert pano["map"]["bounds"]["min_x"] == 1


# ---- the phone's pitch and roll reach the localizer ----------------------------------------


class AttitudeLocalizer(RankedLocalizer):
    seen: list = []

    def update(self, frame_rgb, detections, heading_deg, pitch_deg=None, roll_deg=None):
        type(self).seen.append((heading_deg, pitch_deg, roll_deg))
        return super().update(frame_rgb, detections, heading_deg)


def test_pitch_and_roll_from_the_header_are_passed_to_the_localizer(settings, jpeg):
    from server.protocol import FrameHeader, encode_frame

    AttitudeLocalizer.seen = []
    with TestClient(create_app(settings, localizer_factory=AttitudeLocalizer)) as client:
        with client.websocket_connect("/ws/phone") as phone:
            phone.send_bytes(
                encode_frame(FrameHeader(1, 1.0, 45.0, 48, 64, pitch_deg=-8.0, roll_deg=2.0), jpeg)
            )
            phone.receive_json()
    assert AttitudeLocalizer.seen == [(45.0, -8.0, 2.0)]


def test_localizers_without_attitude_support_still_work(settings, jpeg):
    from server.protocol import FrameHeader, encode_frame

    with TestClient(create_app(settings, localizer_factory=RankedLocalizer)) as client:
        with client.websocket_connect("/ws/phone") as phone:
            phone.send_bytes(
                encode_frame(FrameHeader(1, 1.0, 45.0, 48, 64, pitch_deg=-8.0, roll_deg=2.0), jpeg)
            )
            assert phone.receive_json()["location"]["state"] == "LOCKED"
