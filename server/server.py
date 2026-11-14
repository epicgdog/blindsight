"""FastAPI frame pipeline. Run from the repo with `uv run python -m server.server`."""

from __future__ import annotations

import argparse
import asyncio
import base64
import importlib
import inspect
import io
import json
import logging
import math
import socket
import sys
import time
import uuid
import warnings
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

import anyio
import numpy as np
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field

# Also support the plan's `cd server && uv run python server.py` command.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.config import Settings
from server.cues import CueEngine, describe
from server.detect import Detector, StubDetector
from server.minimap import scan_points
from server.navigation import navigation_router
from server.protocol import FramePacket, ProtocolError, decode_frame
from server.record import SessionRecorder

log = logging.getLogger("blindsight")


def decode_image(packet: FramePacket, settings: Settings) -> tuple[np.ndarray, bytes]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(packet.jpeg)) as source:
                if (
                    source.format != "JPEG"
                    or source.width * source.height > settings.max_image_pixels
                ):
                    log.warning(
                        "Rejected %s frame %dx%d (limit %d pixels)",
                        source.format,
                        source.width,
                        source.height,
                        settings.max_image_pixels,
                    )
                    raise ProtocolError("Invalid JPEG format or image dimensions")
                # JPEG draft mode decodes at a reduced scale, far faster for 12 MP photos.
                source.draft("RGB", (settings.max_frame_side, settings.max_frame_side))
                source.load()
                upright = ImageOps.exif_transpose(source).convert("RGB")
                if max(upright.size) > settings.max_frame_side:
                    upright.thumbnail((settings.max_frame_side, settings.max_frame_side))
                # The real decoded size is authoritative. Expo may report pre-rotation sizes.
                rgb = np.array(upright)
                buffer = io.BytesIO()
                upright.save(buffer, format="JPEG", quality=85)
                return rgb, buffer.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        ValueError,
    ) as exc:
        raise ProtocolError("Unable to decode JPEG safely") from exc


def make_localizer(settings: Settings, detector=None):
    module = "server.localize_stub" if settings.localizer == "stub" else "server.localize"
    try:
        factory = importlib.import_module(module).Localizer
        parameters = inspect.signature(factory).parameters
        kwargs = {"data_dir": settings.data_dir} if "data_dir" in parameters else {}
        if settings.pano_id and "pano_id" in parameters:
            kwargs["pano_id"] = settings.pano_id
        if (
            settings.mask_refs
            and detector is not None
            and not isinstance(detector, StubDetector)
            and "ref_detector" in parameters
        ):
            # Mask people and furniture out of the pano reference views with the live detector.
            kwargs["ref_detector"] = detector.detect
            kwargs["ref_detector_id"] = f"{settings.yolo_model}@{settings.yolo_conf}"
        if "places" in parameters and settings.candidate_places not in ("1", ""):
            kwargs["places"] = settings.candidate_places
        return factory(**kwargs)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Real localizer is unavailable. Use LOCALIZER=stub until Part 3 lands."
        ) from exc


@dataclass
class Warmup:
    deadline: float
    duration_s: float = 20.0
    attempted: int = 0
    accepted: int = 0
    errors: int = 0

    def status(self):
        remaining = max(0.0, self.deadline - time.monotonic())
        result = {
            "active": remaining > 0,
            "remaining_s": remaining,
            "attempted": self.attempted,
            "accepted": self.accepted,
            "errors": self.errors,
        }
        # Match the existing Part 2 viewer's warm-up progress contract.
        result.update(
            running=remaining > 0,
            seconds=self.duration_s,
            elapsed=min(self.duration_s, self.duration_s - remaining),
            kept=self.accepted,
        )
        return result


@dataclass
class Processed:
    packet: FramePacket
    result: dict
    jpeg: bytes
    rgb: np.ndarray
    received_s: float


class Pipeline:
    """All mutable ML state stays on one dedicated worker, never the event loop."""

    def __init__(self, settings: Settings, detector, localizer):
        self.settings = settings
        self.detector = detector
        self.localizer = localizer
        try:
            landmarks = json.loads((settings.data_dir / "cue_landmarks.json").read_text())
            if not isinstance(landmarks, list):
                landmarks = []
        except (OSError, ValueError):
            landmarks = []
        self.cues = CueEngine(
            settings.hfov_deg,
            settings.cooldown_s,
            settings.min_gap_s,
            detailed=settings.detailed_cues,
            landmarks=landmarks,
        )
        self.recorder = SessionRecorder(settings.data_dir / "sessions") if settings.record else None
        self.count = 0
        self.location = None

    def process(self, packet: FramePacket, received_s: float, warmup: Warmup | None) -> Processed:
        start = time.monotonic()
        rgb, jpeg = decode_image(packet, self.settings)
        decoded = time.monotonic()
        detections = self.detector.detect(rgb)
        detected = time.monotonic()
        # Part 3 owns scheduling embeddings and temporal fusion internally. Calling every
        # processed frame preserves heading prediction, failure counts and warm-up evidence.
        self.location = self.localizer.update(rgb, detections, packet.header.heading_deg)
        localized = time.monotonic()
        cue = self.cues.update(detections, self.location, localized)
        if warmup and localized < warmup.deadline:
            warmup.attempted += 1
            try:
                if self.localizer.add_reference(rgb):
                    save_capture(self.settings, "pos", jpeg)
                    warmup.accepted += 1
            except Exception:
                warmup.errors += 1
                log.exception("Warm-up reference rejected after an error")
        result = {
            "seq": packet.header.seq,
            "ts_ms": packet.header.ts_ms,
            "w": rgb.shape[1],
            "h": rgb.shape[0],
            "server_ms": {
                "decode": (decoded - start) * 1000,
                "yolo": (detected - decoded) * 1000,
                "embed": getattr(self.localizer, "last_debug", {}).get(
                    "embed", (localized - detected) * 1000
                ),
                "total": (time.monotonic() - received_s) * 1000,
            },
            "detections": [describe(d, self.settings.hfov_deg) for d in detections],
            "location": self.location.to_dict(),
            "cue": asdict(cue) if cue else None,
            "mode": {"detector": self.settings.detector, "localizer": self.settings.localizer},
            "match": getattr(self.localizer, "last_debug", {}).get("match"),
        }
        # Fail closed on a broken plugin result (e.g. NaN), before sending JSON or recording.
        json.dumps(result, allow_nan=False)
        if self.recorder:
            self.recorder.write(packet, result, received_s)
        result["server_ms"]["total"] = (time.monotonic() - received_s) * 1000
        self.count += 1
        return Processed(packet, result, jpeg, rgb, received_s)

    def close(self):
        if self.recorder:
            self.recorder.close()


def save_capture(settings: Settings, kind: str, jpeg: bytes) -> str:
    folder = settings.data_dir / ("onsite/pos" if kind == "pos" else "negatives")
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.jpg"
    (folder / name).write_bytes(jpeg)
    return str((folder / name).relative_to(settings.data_dir))


def percentiles(values):
    if not values:
        return {"p50": None, "p95": None}
    return dict(zip(("p50", "p95"), map(float, np.percentile(values, [50, 95])), strict=True))


class Metrics:
    def __init__(self, window_s: float):
        self.window_s = window_s
        self.samples = deque(maxlen=10000)
        self.speech = deque(maxlen=10000)
        self.processed = self.received = self.dropped = self.invalid = self.errors = 0
        self.connections = 0
        self.connected_at = None
        self.time_to_locked_ms = None
        self.last_speech_at = None

    def add(self, result: dict):
        now = time.monotonic()
        self.samples.append((now, result["server_ms"]))
        self.processed += 1
        if (
            result["location"]["state"] == "LOCKED"
            and self.time_to_locked_ms is None
            and self.connected_at is not None
        ):
            self.time_to_locked_ms = (now - self.connected_at) * 1000

    def snapshot(self):
        cutoff = time.monotonic() - self.window_s
        for samples in (self.samples, self.speech):
            while samples and samples[0][0] < cutoff:
                samples.popleft()
        samples = list(self.samples)
        elapsed = time.monotonic() - samples[0][0] if samples else 0
        return {
            "window_s": self.window_s,
            "latency_ms": percentiles([s[1]["total"] for s in samples]),
            "stage_ms": {
                stage: percentiles([s[1][stage] for s in samples])
                for stage in ("decode", "yolo", "embed", "total")
            },
            "fps": (len(samples) - 1) / elapsed if len(samples) > 1 and elapsed > 0 else 0.0,
            "capture_to_audible_ms": percentiles([s[1] for s in self.speech]),
            "speech_samples": len(self.speech),
            "speech_acknowledged": self.last_speech_at is not None,
            "processed": self.processed,
            "received": self.received,
            "dropped": self.dropped,
            "invalid": self.invalid,
            "errors": self.errors,
            "connections": self.connections,
            "time_to_locked_ms": self.time_to_locked_ms,
            "latency_scope": "server receive to result; excludes camera/network/phone speech",
            "localization_accuracy": None,  # Requires labeled Part 3 evaluation, never inferred live.
        }


@dataclass
class Runtime:
    settings: Settings
    detector: object
    executor: ThreadPoolExecutor
    metrics: Metrics
    localizer_factory: object
    initial_localizer: object = None
    active_localizer: object = None
    latest: Processed | None = None
    phone_active: bool = False
    viewers: set = field(default_factory=set)
    warmup: Warmup | None = None
    cue_ids: dict = field(default_factory=dict)

    async def work(self, function, *args):
        future = asyncio.get_running_loop().run_in_executor(self.executor, function, *args)
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            # Do not release a connection's model state while its thread still uses it.
            with anyio.CancelScope(shield=True), suppress(Exception):
                await asyncio.shield(future)
            raise

    def publish(self, item: Processed):
        self.latest = item
        self.metrics.add(item.result)
        cutoff = time.monotonic() - 60
        self.cue_ids = {key: stamp for key, stamp in self.cue_ids.items() if stamp >= cutoff}
        if item.result["cue"]:
            self.cue_ids[item.result["cue"]["id"]] = time.monotonic()
        for queue in self.viewers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(item)


def pano_metadata(settings: Settings) -> dict:
    pano_id = settings.pano_id
    root = settings.data_dir / "pano"
    selected = root / "pano.json"
    chosen = {}
    if selected.is_file():
        with suppress(OSError, ValueError):
            chosen = json.loads(selected.read_text())
            pano_id = pano_id or chosen.get("pano_id")
    if pano_id is None and root.is_dir():
        candidates = sorted(p for p in root.iterdir() if p.is_dir())
        if len(candidates) == 1:
            pano_id = candidates[0].name
    pano_id = pano_id or settings.default_pano_id
    label = settings.pano_label or chosen.get("label")
    locations = settings.data_dir / "matterport" / "panoLocations.json"
    if label is None and locations.is_file():
        # Handles either saved list or nested GraphQL response without inventing API fields.
        def find_label(value):
            if isinstance(value, dict):
                if value.get("id") == pano_id:
                    return value.get("label")
                for child in value.values():
                    found = find_label(child)
                    if found:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = find_label(child)
                    if found:
                        return found
            return None

        try:
            label = find_label(json.loads(locations.read_text()))
        except (OSError, ValueError):
            log.warning("Unable to read pano labels")
        if label is not None and str(label).isdigit():
            label = None  # Matterport sweep numbers are not speakable place names
    label = label or settings.default_pano_label
    calibration_file = settings.data_dir / "calibration.json"
    calibration = None
    if calibration_file.is_file():
        with suppress(OSError, ValueError):
            calibration = json.loads(calibration_file.read_text())
    faces = []
    if pano_id and Path(pano_id).name == pano_id:
        for index in range(6):
            filename = f"face_{index}.jpg"
            if (root / pano_id / filename).is_file():
                faces.append(f"/data/pano/{quote(pano_id, safe='')}/{filename}")
    references = []
    if pano_id and Path(pano_id).name == pano_id:
        manifest = root / pano_id / "refs" / "refs.json"
        if manifest.is_file():
            with suppress(OSError, ValueError, KeyError):
                references = [
                    {
                        **ref,
                        "path": f"/data/pano/{quote(pano_id, safe='')}/refs/{quote(ref['file'], safe='')}",
                    }
                    for ref in json.loads(manifest.read_text())["refs"]
                ]
    places = {}
    if chosen.get("pano_id"):
        places[chosen["pano_id"]] = {"room": chosen.get("room"), "label": chosen.get("scan_label")}
    for neg in chosen.get("negatives") or []:
        if isinstance(neg, dict) and neg.get("pano_id"):
            places[neg["pano_id"]] = {"room": neg.get("room"), "label": neg.get("label")}
    return {
        "pano_id": pano_id,
        "label": label,
        "places": places,
        "faces": faces,
        "face_urls": faces,
        "thresholds": calibration,
        "localizer": settings.localizer,
        "refs": references,
        "model_id": chosen.get("model_id") or settings.matterport_model_id,
    }


class SpeechReport(BaseModel):
    cue_id: str | None = Field(default=None, max_length=128)
    played: bool
    capture_to_audible_ms: float | None = Field(default=None, ge=0, le=60000, allow_inf_nan=False)


def create_app(
    settings: Settings | None = None, *, detector=None, localizer_factory=None
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blindsight-inference")
        try:
            actual_detector = detector
            if actual_detector is None:
                factory = (
                    StubDetector if settings.detector == "stub" else lambda: Detector(settings)
                )
                actual_detector = await asyncio.get_running_loop().run_in_executor(
                    executor, factory
                )
            factory = localizer_factory or (lambda: make_localizer(settings, actual_detector))
            # Fail at startup, not on the first phone frame, if the selected plugin cannot load.
            initial_localizer = await asyncio.get_running_loop().run_in_executor(executor, factory)
            runtime = Runtime(
                settings, actual_detector, executor, Metrics(settings.metrics_window_s), factory
            )
            runtime.initial_localizer = initial_localizer
            app.state.runtime = runtime
            log.info(
                "Server ready: detector=%s, localizer=%s", settings.detector, settings.localizer
            )
            yield
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="Blindsight", lifespan=lifespan)
    app.include_router(navigation_router(settings.data_dir))

    @app.get("/")
    async def index():
        path = settings.viewer_dir / "index.html"
        if path.is_file():
            return FileResponse(path)
        return HTMLResponse(
            "<h1>Blindsight core server is ready</h1>"
            "<p>The Part 2 viewer has not been installed.</p>"
            '<p><a href="/metrics">Metrics</a> · <a href="/docs">API</a></p>'
        )

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "detector": settings.detector, "localizer": settings.localizer}

    @app.get("/api/pano")
    async def pano():
        result = await asyncio.to_thread(pano_metadata, settings)
        localizer = app.state.runtime.active_localizer or app.state.runtime.initial_localizer
        if localizer is not None and hasattr(localizer, "s"):
            result["thresholds"] = localizer.s.thresholds()
        regions = getattr(localizer, "masked_regions", None)
        result["masked_regions"] = regions() if callable(regions) else []
        result["map"] = await asyncio.to_thread(scan_points, settings.data_dir)
        return result

    @app.get("/api/floorplan/{floor}")
    async def floorplan(floor: str):
        if not floor.isdigit():
            raise HTTPException(404, "Unknown floor")
        path = settings.data_dir / "matterport" / f"floor{int(floor) + 1}.png"
        if not path.is_file():
            raise HTTPException(404, "No floor plan for that floor")
        return FileResponse(path, media_type="image/png")

    @app.get("/metrics")
    async def metrics():
        runtime = app.state.runtime
        result = runtime.metrics.snapshot()
        result["mode"] = {"detector": settings.detector, "localizer": settings.localizer}
        result["phone_connected"] = runtime.phone_active
        result["warmup"] = runtime.warmup.status() if runtime.warmup else None
        result["p50_ms"] = result["latency_ms"]["p50"]
        result["p95_ms"] = result["latency_ms"]["p95"]
        report = settings.data_dir / "reports" / "localization.json"
        if not report.is_file():
            report = settings.data_dir / "eval" / "results.json"
        if report.is_file():
            with suppress(OSError, ValueError):
                result["localization_accuracy"] = await asyncio.to_thread(
                    lambda: json.loads(report.read_text())
                )
        return result

    @app.post("/api/speech")
    async def speech(report: SpeechReport):
        runtime = app.state.runtime
        if not report.played:
            return {"accepted": False}
        if report.cue_id is not None:
            if report.cue_id not in runtime.cue_ids:
                raise HTTPException(409, "Cue unknown, already acknowledged or expired")
            runtime.cue_ids.pop(report.cue_id)
        elif report.capture_to_audible_ms is not None:
            raise HTTPException(422, "Latency requires a cue_id")
        runtime.metrics.last_speech_at = time.monotonic()
        if report.capture_to_audible_ms is not None:
            runtime.metrics.speech.append((time.monotonic(), report.capture_to_audible_ms))
        return {"accepted": True}

    # Register the literal route BEFORE {kind}, or FastAPI routes 'auto' as a manual capture.
    @app.post("/api/capture/auto")
    async def capture_auto(seconds: float = Query(default=20, gt=0, le=120)):
        runtime = app.state.runtime
        if not runtime.phone_active:
            raise HTTPException(409, "Connect a frame source before starting warm-up")
        if runtime.warmup and runtime.warmup.status()["active"]:
            raise HTTPException(409, "Warm-up already active")
        runtime.warmup = Warmup(time.monotonic() + seconds, seconds)
        return runtime.warmup.status()

    @app.get("/api/capture/auto")
    async def capture_status():
        warmup = app.state.runtime.warmup
        return (
            warmup.status()
            if warmup
            else {
                "active": False,
                "attempted": 0,
                "accepted": 0,
                "errors": 0,
                "remaining_s": 0,
                "running": False,
                "seconds": 0,
                "elapsed": 0,
                "kept": 0,
            }
        )

    @app.post("/api/capture/{kind}")
    async def capture(kind: str):
        if kind not in {"pos", "neg"}:
            raise HTTPException(404, "Capture kind must be pos or neg")
        latest = app.state.runtime.latest
        if latest is None:
            raise HTTPException(409, "No frame available")
        filename = await asyncio.to_thread(save_capture, settings, kind, latest.jpeg)
        localizer = app.state.runtime.active_localizer
        if kind == "neg" and localizer is not None and hasattr(localizer, "add_negative"):
            await app.state.runtime.work(localizer.add_negative, latest.rgb)
        return {"saved": filename, "seq": latest.result["seq"]}

    @app.websocket("/ws/phone")
    async def phone(websocket: WebSocket):
        runtime = app.state.runtime
        await websocket.accept()
        if runtime.phone_active:
            await websocket.close(code=1013, reason="One active camera source is supported")
            return
        runtime.phone_active = True
        runtime.latest = None
        runtime.metrics.connections += 1
        runtime.metrics.connected_at = time.monotonic()
        runtime.metrics.time_to_locked_ms = None
        queue = asyncio.Queue(maxsize=1)
        pipeline = None
        tasks = []

        async def receive():
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                received_s = time.monotonic()
                raw = message.get("bytes")
                if raw is None:
                    runtime.metrics.invalid += 1
                    await websocket.close(1003, "Send binary JPEG frame packets")
                    return
                try:
                    packet = decode_frame(raw, settings.max_message_bytes)
                except ProtocolError:
                    runtime.metrics.invalid += 1
                    await websocket.close(1008, "Invalid frame packet")
                    return
                runtime.metrics.received += 1
                if queue.full():
                    queue.get_nowait()
                    runtime.metrics.dropped += 1
                queue.put_nowait((packet, received_s))

        async def process():
            while True:
                packet, received_s = await queue.get()
                try:
                    item = await runtime.work(pipeline.process, packet, received_s, runtime.warmup)
                except ProtocolError:
                    runtime.metrics.invalid += 1
                    await websocket.close(1008, "Invalid JPEG image")
                    return
                except Exception:
                    runtime.metrics.errors += 1
                    log.exception("Frame pipeline failed")
                    await websocket.close(1011, "Frame processing failed; reconnect to retry")
                    return
                runtime.publish(item)
                await asyncio.wait_for(websocket.send_json(item.result), settings.send_timeout_s)

        try:
            # Start draining the socket before any expensive per-session initialization.
            tasks.append(asyncio.create_task(receive()))
            localizer = runtime.initial_localizer
            if localizer is None:
                localizer = await runtime.work(runtime.localizer_factory)
            # Part 3 offers reset(): retain costly model/reference caches and verified
            # warm-up references while resetting temporal state between phone sessions.
            if callable(getattr(localizer, "reset", None)):
                await runtime.work(localizer.reset)
                runtime.initial_localizer = localizer
            else:
                runtime.initial_localizer = None
            runtime.active_localizer = localizer
            pipeline = await runtime.work(Pipeline, settings, runtime.detector, localizer)
            tasks.append(asyncio.create_task(process()))
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (TimeoutError, WebSocketDisconnect, RuntimeError):
            pass
        finally:
            with anyio.CancelScope(shield=True):
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if pipeline:
                    await runtime.work(pipeline.close)
                if runtime.warmup:
                    runtime.warmup.deadline = 0
                runtime.phone_active = False
                runtime.active_localizer = None
                with suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.close()

    @app.websocket("/ws/viewer")
    async def viewer(websocket: WebSocket):
        runtime = app.state.runtime
        await websocket.accept()
        queue = asyncio.Queue(maxsize=1)
        runtime.viewers.add(queue)
        if runtime.latest:
            queue.put_nowait(runtime.latest)

        async def drain():
            while True:
                if (await websocket.receive())["type"] == "websocket.disconnect":
                    return

        async def send():
            next_send = 0.0
            while True:
                item = await queue.get()
                await asyncio.sleep(max(0.0, next_send - time.monotonic()))
                if not queue.empty():
                    item = queue.get_nowait()
                payload = {**item.result, "jpeg_b64": base64.b64encode(item.jpeg).decode("ascii")}
                await asyncio.wait_for(websocket.send_json(payload), settings.send_timeout_s)
                next_send = time.monotonic() + 1 / settings.viewer_fps

        tasks = [asyncio.create_task(drain()), asyncio.create_task(send())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (TimeoutError, WebSocketDisconnect, RuntimeError):
            pass
        finally:
            with anyio.CancelScope(shield=True):
                runtime.viewers.discard(queue)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                with suppress(RuntimeError, WebSocketDisconnect):
                    await websocket.close()

    # Serve only public pano images, never sessions, credentials or on-site captures.
    app.mount("/data/pano", StaticFiles(directory=settings.data_dir / "pano", check_dir=False))
    app.mount("/viewer", StaticFiles(directory=settings.viewer_dir, check_dir=False))

    @app.get("/{asset:path}")
    async def viewer_asset(asset: str):
        # Relative app.js/style.css URLs in Part 2's index.html work at / as well.
        path = (settings.viewer_dir / asset).resolve()
        if path.is_relative_to(settings.viewer_dir.resolve()) and path.is_file():
            return FileResponse(path)
        raise HTTPException(404)

    return app


def self_check(settings: Settings) -> bool:
    checks = {}
    try:
        Detector(settings)
        checks["YOLO cached weights and inference"] = True
    except Exception as exc:
        print(f"  {exc}")
        checks["YOLO cached weights and inference"] = False
    try:
        make_localizer(settings)
        checks["real localizer loads"] = settings.localizer == "real"
    except Exception:
        checks["real localizer loads"] = False
    metadata = pano_metadata(settings)
    pano_ok = len(metadata["faces"]) == 6
    if pano_ok:
        sizes = []
        try:
            for index in range(6):
                path = settings.data_dir / "pano" / metadata["pano_id"] / f"face_{index}.jpg"
                with Image.open(path) as face:
                    face.verify()
                    sizes.append(face.size)
            pano_ok = len(set(sizes)) == 1 and sizes[0][0] == sizes[0][1]
        except (OSError, ValueError):
            pano_ok = False
    checks["pano loaded (six equal square faces)"] = pano_ok
    calibration = metadata["thresholds"]
    calibration_ok = False
    if isinstance(calibration, dict):
        hi, lo = calibration.get("T_HI"), calibration.get("T_LO")
        calibration_ok = (
            type(hi) in (int, float)
            and type(lo) in (int, float)
            and math.isfinite(hi)
            and math.isfinite(lo)
            and -1 <= lo <= hi <= 1
        )
    checks["calibration loaded (T_LO <= T_HI)"] = calibration_ok
    # A server cannot prove acoustic output. Require an actual phone playback acknowledgment.
    try:
        from urllib.request import urlopen

        with urlopen(f"http://127.0.0.1:{settings.port}/metrics", timeout=2) as response:
            metrics = json.load(response)
        checks["phone speech acknowledged"] = bool(metrics.get("speech_acknowledged"))
    except (OSError, ValueError):
        checks["phone speech acknowledged"] = False
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'} {name}")
    return all(checks.values())


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.self_check:
        raise SystemExit(0 if self_check(settings) else 1)
    addresses = {"127.0.0.1"}
    with suppress(OSError):
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    with suppress(OSError):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))  # Route lookup only; sends no packet.
            addresses.add(probe.getsockname()[0])
    for address in sorted(addresses):
        log.info(
            "Viewer http://%s:%s/ | phone ws://%s:%s/ws/phone",
            address,
            settings.port,
            address,
            settings.port,
        )
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",
        port=settings.port,
        ws_max_size=settings.max_message_bytes,
        ws_max_queue=1,
    )


# Importing the app is cheap; models are initialized only in its lifespan.
app = create_app()

if __name__ == "__main__":
    main()
