"""Debug server: a separate page that shows every stage of localization, frame by frame.

It runs next to the main server and reads its live viewer stream, so nothing about the main server
changes. The page shows the received frame against the matched reference view with the matched
points, the 2D and 3D maps with the PnP fix, the tracked position, heading and route, and a table for
each stage (input, retrieval, features, verification, decision, mesh PnP, tracker).

  uv run python3 -m server.debug_server                     # http://localhost:8001
  MAIN_URL=http://127.0.0.1:8000 DEBUG_PORT=8001 uv run python3 -m server.debug_server
"""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path

import httpx
import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

MAIN_URL = os.environ.get("MAIN_URL", "http://127.0.0.1:8000").rstrip("/")
PORT = int(os.environ.get("DEBUG_PORT", "8001"))
HERE = Path(__file__).resolve().parent / "debug"

app = FastAPI(title="Blindsight debug")
client = httpx.AsyncClient(base_url=MAIN_URL, timeout=15.0)


@app.get("/")
async def index():
    return FileResponse(HERE / "index.html")


@app.get("/debug/{name}")
async def asset(name: str):
    path = (HERE / name).resolve()
    if path.parent != HERE or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


async def proxy(path: str, request: Request) -> Response:
    """GET from the main server at the same path (maps, reference images, viewer scripts)."""
    try:
        r = await client.get("/" + path, params=dict(request.query_params))
    except httpx.HTTPError as ex:
        raise HTTPException(502, f"main server unreachable at {MAIN_URL}: {type(ex).__name__}") from ex
    keep = {k: v for k, v in r.headers.items() if k.lower() in ("content-type", "cache-control")}
    return Response(r.content, status_code=r.status_code, headers=keep)


@app.get("/api/{path:path}")
async def api(path: str, request: Request):
    return await proxy("api/" + path, request)


@app.get("/viewer/{path:path}")
async def viewer(path: str, request: Request):
    return await proxy("viewer/" + path, request)


@app.get("/data/{path:path}")
async def data(path: str, request: Request):
    return await proxy("data/" + path, request)


@app.websocket("/ws")
async def relay(ws: WebSocket):
    """The main server's /ws/viewer stream (one upstream per page), passed through unchanged."""
    await ws.accept()
    url = MAIN_URL.replace("http", "ws", 1) + "/ws/viewer"
    try:
        async with websockets.connect(url, max_size=16_000_000) as up:
            async def down():
                while True:
                    if (await ws.receive())["type"] == "websocket.disconnect":
                        return

            async def forward():
                with suppress(WebSocketDisconnect, RuntimeError, websockets.WebSocketException):
                    async for msg in up:
                        await ws.send_text(msg if isinstance(msg, str) else msg.decode())

            tasks = [asyncio.create_task(down()), asyncio.create_task(forward())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in tasks:
                t.cancel()
    except (OSError, websockets.WebSocketException, WebSocketDisconnect):
        pass
    finally:
        with suppress(RuntimeError):
            await ws.close()


def main():
    import uvicorn

    print(f"Blindsight debug: http://localhost:{PORT}/  (reading {MAIN_URL})")
    uvicorn.run(app, host="0.0.0.0", port=PORT, ws_max_size=16_000_000)


if __name__ == "__main__":
    main()
