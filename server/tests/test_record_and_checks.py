import json
from dataclasses import replace

import pytest

from server.protocol import decode_frame
from server.record import SessionRecorder, replay_frames
from tools.sim_client import demo_failures, parse_args, source_frames


def test_recording_uses_receipt_timing_and_unique_filenames(tmp_path, packet):
    recorder = SessionRecorder(tmp_path)
    for receipt in (100, 100.25, 100.5):
        recorder.write(decode_frame(packet(1)), {"cue": None}, receipt)
    recorder.close()
    frames = list(replay_frames(recorder.path))
    assert [offset for offset, _ in frames] == [0, 0.25, 0.5]
    assert len(list(recorder.path.glob("*.jpg"))) == 3
    args = parse_args(["--replay", str(recorder.path)])
    assert list(source_frames(args)) == frames


@pytest.mark.parametrize(
    "offset,filename",
    [(-1, "frame.jpg"), (float("nan"), "frame.jpg"), (0, "../escape.jpg"), (0, "/absolute.jpg")],
)
def test_invalid_replay_rejected(tmp_path, packet, offset, filename):
    recorder = SessionRecorder(tmp_path)
    recorder.write(decode_frame(packet()), {}, 1)
    recorder.close()
    manifest = recorder.path / "frames.jsonl"
    row = json.loads(manifest.read_text())
    row.update(offset_s=offset, file=filename)
    manifest.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError):
        list(replay_frames(recorder.path))


def full_evidence():
    report = {
        "mode": {"detector": "yolo", "localizer": "real"},
        "fps": 6,
        "rtt_ms": {"p95": 100},
        "time_to_locked_ms": 1000,
    }
    metrics = {"capture_to_audible_ms": {"p50": 300, "p95": 600}}
    evaluation = {
        "false_locked_rate": 0,
        "recall": 0.95,
        "negative_walkthroughs": 20,
        "false_locked_walkthroughs": 0,
        "locked_within_2s_trials": 20,
        "yaw_median_deg": 5,
        "yaw_p95_deg": 15,
        "split": "held-out",
        "simulated": True,
    }
    return report, metrics, evaluation


def test_demo_check_requires_complete_evidence():
    assert demo_failures(*full_evidence()) == []
    report, metrics, evaluation = full_evidence()
    assert demo_failures(report, {}, evaluation)
    assert demo_failures(report, metrics, {})
    report["mode"]["localizer"] = "stub"
    assert demo_failures(report, metrics, evaluation)


@pytest.mark.parametrize(
    "field,value",
    [
        ("false_locked_rate", 0.01),
        ("recall", 0.89),
        ("false_locked_walkthroughs", 1),
        ("negative_walkthroughs", 19),
        ("locked_within_2s_trials", 19),
        ("yaw_median_deg", 8),
        ("yaw_p95_deg", 20),
        ("recall", float("nan")),
        ("simulated", False),
    ],
)
def test_demo_check_rejects_regressions(field, value):
    report, metrics, evaluation = full_evidence()
    evaluation[field] = value
    assert demo_failures(report, metrics, evaluation)


def test_image_directory_source_and_args(tmp_path):
    from PIL import Image

    Image.new("RGB", (480, 640), "red").save(tmp_path / "1.png")
    args = parse_args(["--images", str(tmp_path), "--fps", "5"])
    frames = list(source_frames(args))
    assert len(frames) == 1
    assert frames[0][1].header.w == 480 and frames[0][1].header.h == 640
    with pytest.raises(SystemExit):
        parse_args(["--fps", "nan"])
    with pytest.raises(SystemExit):
        parse_args(["--soak"])


def test_oversized_images_rejected(settings, packet):
    from server.protocol import ProtocolError
    from server.server import decode_image

    with pytest.raises(ProtocolError):
        decode_image(decode_frame(packet()), replace(settings, max_image_pixels=100))


@pytest.mark.parametrize(
    "overrides",
    [
        {"cooldown_s": float("nan")},
        {"min_gap_s": float("inf")},
        {"pano_id": ".."},
        {"pano_id": "../secret"},
        {"send_timeout_s": 0},
    ],
)
def test_invalid_settings_fail_early(settings, overrides):
    with pytest.raises(ValueError):
        replace(settings, **overrides)


def test_cold_start_check_requires_real_assets(settings, monkeypatch, capsys):
    import io
    import urllib.request

    from PIL import Image

    import server.server as module

    settings = replace(settings, localizer="real", pano_id="test")
    monkeypatch.setattr(module, "Detector", lambda _: object())
    monkeypatch.setattr(module, "make_localizer", lambda _: object())
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **kw: io.StringIO('{"speech_acknowledged":true}')
    )
    assert not module.self_check(settings)
    assert "FAIL pano" in capsys.readouterr().out
    root = settings.data_dir / "pano/test"
    root.mkdir(parents=True)
    for index in range(6):
        Image.new("RGB", (10, 10)).save(root / f"face_{index}.jpg")
    calibration = settings.data_dir / "calibration.json"
    calibration.write_text('{"T_HI":0.8,"T_LO":0.6}')
    assert module.self_check(settings)
    calibration.write_text('{"T_HI":0.5,"T_LO":0.9}')
    assert not module.self_check(settings)
    calibration.write_text('{"T_HI":0.8,"T_LO":0.6}')
    (root / "face_0.jpg").write_bytes(b"not JPEG")
    assert not module.self_check(settings)
