import io

import pytest
from PIL import Image

from server.config import Settings
from server.protocol import FrameHeader, encode_frame


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path, detector="stub", localizer="stub", viewer_dir=tmp_path / "viewer"
    )


@pytest.fixture
def jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (48, 64), (180, 70, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def packet(jpeg):
    return lambda seq=0: encode_frame(FrameHeader(seq, 123.5, None, 48, 64), jpeg)
