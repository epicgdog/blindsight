"""Part 2 stand-in server: speaks contracts 5.1/5.2/5.3 with canned results.

Run: uv run python3 app/dev/echo_server.py   (port from BLINDSIGHT_PORT, default 8000)
Swapping to Part 1's real server is a URL change only.
"""
import asyncio
import base64
import json
import os
import struct
import time
from collections import deque
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]
VIEWER = ROOT / "server" / "viewer"
DATA = Path(os.environ.get("BLINDSIGHT_DATA", ROOT / "data"))
PANO_LABEL = "Main lobby"

app = FastAPI()
viewers: set = set()
latest = {"jpeg": None}
lat = deque(maxlen=200)
frame_times = deque(maxlen=50)
auto = {"running": False, "started": 0.0, "seconds": 0, "kept": 0}
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACv/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q==")


def result(seq: int, n: int, ms: float = 1.0) -> dict:
    cue = None
    if n == 0 or n % 25 == 0:
        cue = {"id": "loc-main-lobby", "text": "You are in the main lobby", "priority": 2, "interrupt": True}
    elif n % 10 == 0:
        cue = {"id": f"obj-chair-{n}", "text": "Chair, ahead, close", "priority": 1, "interrupt": False}
    return {
        "seq": seq,
        "server_ms": {"decode": 1.0, "yolo": 2.0, "embed": 1.0, "total": ms},
        "detections": [{"cls": "chair", "conf": 0.91, "bbox": [0.3, 0.4, 0.6, 0.9],
                        "direction": "ahead", "distance": "close"}],
        "location": {"pano_id": "6sd6A5r5EVp-demo", "label": PANO_LABEL, "score": 0.82, "ema": 0.8,
                     "best_ref": 2, "state": "LOCKED", "inliers": 57, "yaw_deg": 12.0, "yaw_conf": 0.9},
        "cue": cue,
        "match": {
            "ranking": [
                {"place": "6sd6A5r5EVp-demo", "sim": 0.91, "ref": 12, "inliers": 57},
                {"place": "echo-neighbour-1", "sim": 0.64, "ref": 140, "inliers": 6},
                {"place": "echo-neighbour-2", "sim": 0.52, "ref": 201, "inliers": 2},
                {"place": "echo-neighbour-3", "sim": 0.41, "ref": 305, "inliers": 0},
            ],
            "footprint": [[0.2, 0.3], [0.25, 0.3], [0.3, 0.3], [0.3, 0.5], [0.3, 0.7],
                          [0.25, 0.7], [0.2, 0.7], [0.2, 0.5]],
        },
    }


def parse_frame(msg: bytes):
    if len(msg) < 4:
        return None
    (n,) = struct.unpack(">I", msg[:4])
    if len(msg) < 4 + n:
        return None
    try:
        return json.loads(msg[4:4 + n]), msg[4 + n:]
    except ValueError:
        return None


async def push_viewers(res: dict, jpeg: bytes):
    out = json.dumps({**res, "jpeg_b64": base64.b64encode(jpeg).decode()})
    for v in list(viewers):
        try:
            await v.send_text(out)
        except Exception:
            viewers.discard(v)


@app.websocket("/ws/phone")
async def ws_phone(ws: WebSocket):
    await ws.accept()
    n = 0
    try:
        while True:
            parsed = parse_frame(await ws.receive_bytes())
            if parsed is None:
                continue
            header, jpeg = parsed
            t0 = time.perf_counter()
            latest["jpeg"] = jpeg
            res = result(header.get("seq", 0), n, ms=(time.perf_counter() - t0) * 1000)
            n += 1
            lat.append(res["server_ms"]["total"]); frame_times.append(time.time())
            if auto["running"]:
                auto["kept"] += 1
            await ws.send_text(json.dumps(res))
            await push_viewers(res, jpeg)
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/viewer")
async def ws_viewer(ws: WebSocket):
    await ws.accept()
    viewers.add(ws)
    if ws.query_params.get("demo"):
        await push_viewers(result(1, 0), TINY_JPEG)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        viewers.discard(ws)


@app.get("/api/pano")
def pano():
    return {"label": PANO_LABEL, "places": {
        "6sd6A5r5EVp-demo": {"room": "Hallway", "label": "0"},
        "echo-neighbour-1": {"room": "Other / Hallway", "label": "34"},
        "echo-neighbour-2": {"room": "Stairwell", "label": "12"},
    }, "map": {
        "points": [
            {"id": "6sd6A5r5EVp-demo", "label": "0", "room": "Hallway", "x": 0.0, "y": 0.0, "z": 1.3, "floor": 0},
            {"id": "echo-neighbour-1", "label": "34", "room": "Other / Hallway", "x": 3.0, "y": -2.0, "z": 1.3, "floor": 0},
            {"id": "echo-neighbour-2", "label": "12", "room": "Stairwell", "x": -4.0, "y": 3.0, "z": 1.3, "floor": 0},
            {"id": "echo-neighbour-3", "label": "7", "room": None, "x": 6.0, "y": 4.0, "z": 1.3, "floor": 0},
            {"id": "echo-upper", "label": "9", "room": "Upper", "x": 1.0, "y": 1.0, "z": 5.3, "floor": 1},
        ],
        "floors": [{"floor": 0, "z": 1.3, "count": 4}, {"floor": 1, "z": 5.3, "count": 1}],
        "bounds": {"min_x": -4.0, "max_x": 6.0, "min_y": -2.0, "max_y": 4.0},
    }, "masked_regions": [
        [[0.40, 0.55], [0.44, 0.55], [0.44, 0.70], [0.40, 0.70]],
        [[0.62, 0.50], [0.66, 0.50], [0.66, 0.68], [0.62, 0.68]],
    ], "faces": [f"/pano/face_{i}.jpg" for i in range(6)],
            "thresholds": {"locked": 0.7, "uncertain": 0.4}}


@app.get("/metrics")
def metrics():
    s = sorted(lat) or [0.0]
    fps = 0.0
    if len(frame_times) > 1 and frame_times[-1] > frame_times[0]:
        fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
    return {"p50_ms": s[len(s) // 2], "p95_ms": s[min(len(s) - 1, int(len(s) * 0.95))], "fps": fps}


@app.post("/api/capture/auto")
async def capture_auto(seconds: int = 20):
    auto.update(running=True, started=time.time(), seconds=seconds, kept=0)

    async def finish():
        await asyncio.sleep(seconds)
        auto["running"] = False
    asyncio.create_task(finish())
    return {"seconds": seconds, "kept": 0, "running": True}


@app.get("/api/capture/auto")
def capture_auto_status():
    elapsed = min(time.time() - auto["started"], auto["seconds"]) if auto["started"] else 0
    return {**auto, "elapsed": round(elapsed, 2)}


@app.post("/api/capture/{kind}")
def capture(kind: str):
    if kind not in ("pos", "neg"):
        return JSONResponse({"error": "kind must be pos or neg"}, status_code=404)
    if latest["jpeg"] is None:
        return {"saved": False, "reason": "no frame yet"}
    d = DATA / ("onsite/pos" if kind == "pos" else "negatives")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{int(time.time() * 1000)}.jpg"
    p.write_bytes(latest["jpeg"])
    return {"saved": True, "path": str(p.relative_to(DATA.parent)) if DATA.parent in p.parents else p.name}


@app.get("/")
def index():
    return FileResponse(VIEWER / "index.html")


app.mount("/viewer", StaticFiles(directory=VIEWER), name="viewer")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("BLINDSIGHT_PORT", 8000)), log_level="warning")
