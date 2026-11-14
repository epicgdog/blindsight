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
