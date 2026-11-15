"""Dev runner: build the models once, keep them in RAM, hot-reload the rest of the server.

    LOCALIZER=real LOCALIZATION_SCOPE=single uv run python3 -m server.devserver

A normal start spends 1.5-3 min building the localizer (rendering ~4 700 pano reference views,
the retrieval index, feature caches) and loading YOLO. This process does that once. When a .py
file under server/ changes, it stops the web app, re-imports every server module except the
model ones below, and serves a fresh app with the same localizer and detector objects: a code
edit costs about a second instead of minutes. The phone reconnects on its own.

Editing a model module (KEEP) still needs a full restart (Ctrl-C and run again); the runner
says so when it sees one change.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
from pathlib import Path

import uvicorn
from watchfiles import awatch

log = logging.getLogger("insight.dev")

SERVER_DIR = Path(__file__).resolve().parent
# Modules whose objects live in RAM across reloads. Re-importing them would make the kept
# localizer/detector instances belong to stale classes, so they are never reloaded.
KEEP = {
    "server", "server.devserver", "server.localize", "server.property_localize", "server.localize_stub",
    "server.refs", "server.geometry", "server.position", "server.tracker", "server.detect",
    "server.detect_onnx", "server.config",
}


def fresh_server_module():
    """Drop every reloadable server.* module, then import server.server anew."""
    for name in [m for m in sys.modules if (m == "server" or m.startswith("server.")) and m not in KEEP]:
        del sys.modules[name]
    importlib.invalidate_caches()
    return importlib.import_module("server.server")


def build_models(srv, settings):
    from server.detect import StubDetector

    detector = StubDetector() if settings.detector == "stub" else srv.make_detector(settings)
    localizer = srv.make_localizer(settings, detector)
    return detector, localizer


async def serve_until_change(app, settings) -> set:
    config = uvicorn.Config(app, host="0.0.0.0", port=settings.port, ws_max_size=settings.max_message_bytes,
                            ws_max_queue=1, log_level="info")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    changed: set = set()
    watcher = asyncio.create_task(_next_change(changed))
    done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
    if task in done:  # server stopped by itself (Ctrl-C or a crash)
        watcher.cancel()
        task.result()
        raise SystemExit(0)
    server.should_exit = True
    await task
    return changed


async def _next_change(out: set) -> None:
    async for changes in awatch(SERVER_DIR, watch_filter=lambda _c, p: p.endswith(".py") and "/tests/" not in p):
        out.update(Path(p) for _c, p in changes)
        return


def module_name(path: Path) -> str:
    rel = path.resolve().relative_to(SERVER_DIR.parent).with_suffix("")
    return ".".join(rel.parts)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    srv = fresh_server_module()
    settings = srv.Settings()
    log.info("Building models once (this is the slow part)...")
    detector, localizer = build_models(srv, settings)
    log.info("Models ready and kept in RAM. Edit server/*.py to hot-reload.")
    while True:
        app = srv.create_app(settings, detector=detector, localizer_factory=lambda: localizer)
        changed = await serve_until_change(app, settings)
        kept = sorted(m for m in map(module_name, changed) if m in KEEP)
        if kept:
            log.warning("Model module changed (%s): restart the dev server to load it.", ", ".join(kept))
        log.info("Reloading server code (%s)", ", ".join(sorted(p.name for p in changed)))
        try:
            srv = fresh_server_module()
            settings = srv.Settings()
        except Exception:  # noqa: BLE001 - a syntax error must not lose the models
            log.exception("Reload failed; fix the file and save again")
            # Wait for the next edit with the old code still serving.
            continue


if __name__ == "__main__":
    asyncio.run(main())
