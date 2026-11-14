"""Viewer: static, offline, live-updating from the echo server."""
import re
import shutil
import subprocess

import pytest

from .conftest import ROOT

VIEWER = ROOT / "server/viewer"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def test_files_exist():
    for f in ("index.html", "app.js", "style.css", "logic.js"):
        assert (VIEWER / f).is_file(), f


def test_no_external_requests():
    pat = re.compile(r"""(?:src|href)\s*=\s*["']\s*(?:https?:)?//|url\(\s*["']?(?:https?:)?//|from\s+["']https?://|import\(["']https?://""")
    for f in VIEWER.rglob("*"):
        if f.is_file():
            assert not pat.search(f.read_text()), f"external ref in {f}"


def test_required_panels_present():
    html = (VIEWER / "index.html").read_text()
    for el in ("live-frame", "boxes", "state-badge", "score", "sparkline", "ref-strip",
               "inliers", "yaw", "cue-log", "latency", "fps",
               "btn-pos", "btn-neg", "btn-warmup", "warmup-progress"):
        assert f'id="{el}"' in html, el


def test_node_logic_unit():
    r = subprocess.run(["node", "--test", "server/viewer/test"], cwd=ROOT,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.browser
@pytest.mark.skipif(not shutil.which("node") or not __import__("os").path.exists(CHROME),
                    reason="needs Chrome")
def test_viewer_renders_live_state_in_headless_chrome(server):
    """Echo server pushes a canned LOCKED result to each new viewer socket."""
    r = subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=5000", "--dump-dom", server["base"] + "/?demo=1"],
        capture_output=True, text=True, timeout=60,
    )
    dom = r.stdout
    assert re.search(r'id="state-badge"[^>]*>\s*LOCKED', dom), dom[:2000]
    assert re.search(r'id="cue-log".*main lobby', dom, re.S | re.I)
    assert re.search(r'id="inliers"[^>]*>[^<]*\d', dom)
