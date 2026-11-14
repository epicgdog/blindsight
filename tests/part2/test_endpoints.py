"""HTTP endpoints the viewer and app rely on (contract 5.3)."""
import asyncio
import base64
import json

import httpx
import websockets

from .conftest import make_frame


def test_viewer_index_served(server):
    r = httpx.get(server["base"] + "/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    for asset, mime in (("app.js", "javascript"), ("style.css", "css")):
        a = httpx.get(f"{server['base']}/viewer/{asset}")
        assert a.status_code == 200 and mime in a.headers["content-type"]


def test_api_pano(server):
    p = httpx.get(server["base"] + "/api/pano").json()
    assert {"label", "faces", "thresholds"} <= set(p)
    assert len(p["faces"]) == 6


def test_metrics(server):
    m = httpx.get(server["base"] + "/metrics").json()
    assert {"p50_ms", "p95_ms", "fps"} <= set(m)


def test_capture_requires_a_frame_then_saves(server, jpeg_bytes, tmp_path):
    async def push():
        async with websockets.connect(server["ws"] + "/ws/phone") as ws:
            hdr = {"seq": 1, "ts_ms": 1, "heading_deg": None, "w": 160, "h": 120}
            await ws.send(make_frame(hdr, jpeg_bytes))
            await ws.recv()
    asyncio.run(push())
    for kind in ("pos", "neg"):
        r = httpx.post(f"{server['base']}/api/capture/{kind}")
        assert r.status_code == 200 and r.json()["saved"] is True
    assert httpx.post(server["base"] + "/api/capture/bogus").status_code == 404


def test_capture_auto_reports_progress(server):
    r = httpx.post(server["base"] + "/api/capture/auto?seconds=1")
    assert r.status_code == 200
    body = r.json()
    assert {"seconds", "kept"} <= set(body)
    status = httpx.get(server["base"] + "/api/capture/auto").json()
    assert {"running", "elapsed", "seconds", "kept"} <= set(status)


def test_viewer_socket_gets_results_and_jpeg(server, jpeg_bytes):
    async def run():
        async with websockets.connect(server["ws"] + "/ws/viewer") as viewer:
            async with websockets.connect(server["ws"] + "/ws/phone") as phone:
                hdr = {"seq": 3, "ts_ms": 1, "heading_deg": None, "w": 160, "h": 120}
                await phone.send(make_frame(hdr, jpeg_bytes))
                await phone.recv()
                msg = json.loads(await asyncio.wait_for(viewer.recv(), 5))
                return msg
    msg = asyncio.run(run())
    assert msg["seq"] == 3 and "location" in msg and "detections" in msg
    assert base64.b64decode(msg["jpeg_b64"])[:2] == b"\xff\xd8"
