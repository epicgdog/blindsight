"""Echo server speaks contract 5.1 (in) and 5.2 (out) exactly."""
import asyncio
import json

import pytest
import websockets

from .conftest import make_frame

DETECTION_KEYS = {"cls", "conf", "bbox", "direction", "distance"}
LOCATION_KEYS = {"pano_id", "label", "score", "ema", "best_ref", "state", "inliers", "yaw_deg", "yaw_conf"}
SERVER_MS_KEYS = {"decode", "yolo", "embed", "total"}


async def _roundtrip(server, frame):
    async with websockets.connect(server["ws"] + "/ws/phone") as ws:
        await ws.send(frame)
        return json.loads(await asyncio.wait_for(ws.recv(), 5))


def test_result_schema(server, jpeg_bytes):
    hdr = {"seq": 7, "ts_ms": 1000, "heading_deg": 12.5, "w": 160, "h": 120}
    res = asyncio.run(_roundtrip(server, make_frame(hdr, jpeg_bytes)))
    assert res["seq"] == 7
    assert SERVER_MS_KEYS <= set(res["server_ms"])
    assert res["detections"], "canned result must include a detection"
    for d in res["detections"]:
        assert DETECTION_KEYS <= set(d)
        x1, y1, x2, y2 = d["bbox"]
        assert 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1
    loc = res["location"]
    assert LOCATION_KEYS <= set(loc)
    assert loc["state"] in {"LOCKED", "UNCERTAIN", "LOST"}
    cue = res["cue"]
    assert cue is None or {"id", "text", "priority", "interrupt"} <= set(cue)


def test_null_heading_accepted(server, jpeg_bytes):
    hdr = {"seq": 1, "ts_ms": 5, "heading_deg": None, "w": 160, "h": 120}
    res = asyncio.run(_roundtrip(server, make_frame(hdr, jpeg_bytes)))
    assert res["seq"] == 1


def test_first_cue_is_location_cue(server, jpeg_bytes):
    """Definition of done: location cue within the first responses."""
    async def run():
        async with websockets.connect(server["ws"] + "/ws/phone") as ws:
            for seq in range(1, 4):
                hdr = {"seq": seq, "ts_ms": seq, "heading_deg": None, "w": 160, "h": 120}
                await ws.send(make_frame(hdr, jpeg_bytes))
                res = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if res["cue"]:
                    return res["cue"]
    cue = asyncio.run(run())
    assert cue and "main lobby" in cue["text"].lower()


def test_malformed_frame_does_not_kill_connection(server, jpeg_bytes):
    async def run():
        async with websockets.connect(server["ws"] + "/ws/phone") as ws:
            await ws.send(b"\x00")  # too short
            await ws.send(b"\x00\x00\x00\xff{}")  # header length lies
            hdr = {"seq": 9, "ts_ms": 1, "heading_deg": None, "w": 160, "h": 120}
            await ws.send(make_frame(hdr, jpeg_bytes))
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if msg.get("seq") == 9:
                    return msg
    assert asyncio.run(run())["seq"] == 9
