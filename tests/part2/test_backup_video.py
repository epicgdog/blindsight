"""tools/render_backup_video.py produces a labeled, ~N second mp4."""
import json
import subprocess
import sys

import pytest

from .conftest import ROOT

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


@pytest.mark.browser
@pytest.mark.skipif(not __import__("os").path.exists(CHROME), reason="needs Chrome")
def test_renders_short_labeled_video(tmp_path):
    out = tmp_path / "backup.mp4"
    r = subprocess.run(
        [sys.executable, "tools/render_backup_video.py", "--seconds", "3", "--fps", "4", "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, timeout=240,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height",
         "-of", "json", str(out)], capture_output=True, text=True)
    info = json.loads(probe.stdout)
    assert 2.0 <= float(info["format"]["duration"]) <= 5.0
    assert info["streams"][0]["codec_name"] == "h264"
    assert "SIMULATED" in (r.stdout + r.stderr).upper()
