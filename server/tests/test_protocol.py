import io
import json
import struct

import pytest
from PIL import Image

from server.protocol import Detection, FrameHeader, ProtocolError, decode_frame, encode_frame
from server.server import decode_image


def test_roundtrip(jpeg):
    header = FrameHeader(42, 100.25, 359.9, 48, 64)
    packet = decode_frame(encode_frame(header, jpeg))
    assert packet.header == header
    assert packet.jpeg == jpeg


@pytest.mark.parametrize("data", [b"", b"text", b"\x00\x01\x00\x00junk", b"\x00\x00\x00\x02{}"])
def test_invalid_packet(data):
    with pytest.raises(ProtocolError):
        decode_frame(data)


@pytest.mark.parametrize(
    "key,value",
    [
        ("seq", -1),
        ("seq", True),
        ("seq", 2**53),
        ("w", 0),
        ("h", "2"),
        ("heading_deg", 360),
        ("heading_deg", float("nan")),
        ("ts_ms", float("inf")),
    ],
)
def test_header_validation(key, value, jpeg):
    header = {"seq": 0, "ts_ms": 1, "heading_deg": None, "w": 48, "h": 64}
    header[key] = value
    raw = json.dumps(header).encode()
    with pytest.raises(ProtocolError):
        decode_frame(struct.pack(">I", len(raw)) + raw + jpeg)


def test_non_jpeg_and_size_limit(jpeg):
    header = FrameHeader(0, 1, None, 48, 64)
    with pytest.raises(ProtocolError):
        encode_frame(header, b"not a JPEG")
    with pytest.raises(ProtocolError):
        decode_frame(encode_frame(header, jpeg), max_bytes=10)


def test_exif_upright_and_rgb(settings):
    image = Image.new("RGB", (64, 48), (240, 0, 0))
    exif = image.getexif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    packet = decode_frame(encode_frame(FrameHeader(0, 1, None, 64, 48), buffer.getvalue()))
    rgb, jpeg = decode_image(packet, settings)
    assert rgb.shape == (64, 48, 3)
    assert rgb[0, 0, 0] > 200 and rgb[0, 0, 2] < 20
    with Image.open(io.BytesIO(jpeg)) as result:
        assert result.size == (48, 64)
        assert result.getexif().get(274) is None


def test_corrupt_image(settings):
    packet = decode_frame(encode_frame(FrameHeader(0, 1, None, 48, 64), b"\xff\xd8broken"))
    with pytest.raises(ProtocolError):
        decode_image(packet, settings)


@pytest.mark.parametrize("bbox", [(-0.1, 0, 1, 1), (0, 1, 1, 0), (0, 0, float("nan"), 1)])
def test_invalid_detection(bbox):
    with pytest.raises(ValueError):
        Detection("chair", 0.9, bbox)


# ---- optional camera attitude from the phone's accelerometer ------------------------------


def test_header_accepts_optional_pitch_and_roll():
    from server.protocol import FrameHeader, decode_frame, encode_frame

    jpeg = b"\xff\xd8" + b"0" * 10
    h = FrameHeader(1, 5.0, 90.0, 10, 20, pitch_deg=-12.5, roll_deg=3.0)
    out = decode_frame(encode_frame(h, jpeg)).header
    assert (out.pitch_deg, out.roll_deg) == (-12.5, 3.0)


def test_old_headers_without_attitude_still_decode():
    import json
    import struct

    from server.protocol import decode_frame

    raw = json.dumps({"seq": 1, "ts_ms": 1, "heading_deg": None, "w": 4, "h": 4}).encode()
    pkt = decode_frame(struct.pack(">I", len(raw)) + raw + b"\xff\xd8xx")
    assert pkt.header.pitch_deg is None and pkt.header.roll_deg is None


@pytest.mark.parametrize("pitch,roll", [(91, 0), (-91, 0), (0, 181), (float("nan"), 0), ("10", 0)])
def test_attitude_out_of_range_is_rejected(pitch, roll):
    from server.protocol import FrameHeader, ProtocolError

    with pytest.raises(ProtocolError):
        FrameHeader(1, 5.0, None, 10, 20, pitch_deg=pitch, roll_deg=roll)


def test_header_moving_flag_round_trips_and_defaults_to_none():
    jpeg = b"\xff\xd8" + b"0" * 10
    for moving in (True, False, None):
        out = decode_frame(encode_frame(FrameHeader(1, 5.0, None, 10, 20, moving=moving), jpeg)).header
        assert out.moving is moving
    raw = json.dumps({"seq": 1, "ts_ms": 1, "heading_deg": None, "w": 4, "h": 4}).encode()
    assert decode_frame(struct.pack(">I", len(raw)) + raw + b"\xff\xd8xx").header.moving is None


@pytest.mark.parametrize("moving", [1, 0, "false", 0.0])
def test_moving_must_be_boolean(moving):
    with pytest.raises(ProtocolError):
        FrameHeader(1, 5.0, None, 10, 20, moving=moving)
