"""Phone-protocol simulator, replay, benchmark and ten-by-three-minute soak runner."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import io
import json
import math
import sys
import time
from dataclasses import replace
from pathlib import Path
from urllib.request import urlopen

import numpy as np
from PIL import Image, ImageOps
from websockets.asyncio.client import connect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.protocol import FramePacket, encode_frame
from server.record import new_header, replay_frames


def image_packet(image: Image.Image, seq: int = 0) -> FramePacket:
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((960, 960))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=75)
    return FramePacket(new_header(seq, image.width, image.height), buffer.getvalue())


def source_frames(args):
    """Yield (offset, packet). Sources stay lazy; a long recording fits in memory."""
    if args.replay:
        base = 0.0
        while True:
            count = 0
            last = 0.0
            for offset, packet in replay_frames(args.replay):
                last = offset
                count += 1
                yield base + offset, packet
            if not count:
                raise ValueError("Replay session is empty")
            if not args.loop:
                return
            base += last + 1 / args.fps
    elif args.images:
        headings = {}
        manifest = args.images / "frames.jsonl"
        if manifest.is_file():
            for line in manifest.read_text().splitlines():
                row = json.loads(line)
                headings[row["file"]] = row.get("heading_deg")
        files = sorted(
            p for p in args.images.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
        )
        if not files:
            raise ValueError("Image directory contains no JPEG or PNG files")
        seq = 0
        while True:
            for path in files:
                with Image.open(path) as image:
                    packet = image_packet(image, seq)
                if path.name in headings:
                    packet = FramePacket(
                        replace(packet.header, heading_deg=headings[path.name]), packet.jpeg
                    )
                yield seq / args.fps, packet
                seq += 1
            if not args.loop:
                return
    elif args.synthetic:
        # Part 3 hook: iter_frames(fps, width, height) yields RGB uint8 arrays, or
        # (RGB, heading_deg). A finite generator is restarted only with --loop.
        module = importlib.import_module("tools.synthetic_feed")
        factory = getattr(module, "iter_frames", None)
        if factory is not None:

            def frames():
                return factory(fps=args.fps, width=480, height=640)
        elif hasattr(module, "iter_feed"):

            def frames():
                return module.iter_feed(fps=args.fps, seconds=args.seconds or 36, size=(480, 640))
        else:
            raise ValueError("Part 3 synthetic_feed must export iter_feed() or iter_frames()")
        seq = 0
        while True:
            count = 0
            for frame in frames():
                heading = None
                if hasattr(frame, "frame_rgb"):
                    heading, frame = frame.heading_deg, frame.frame_rgb
                elif isinstance(frame, tuple):
                    frame, heading = frame
                packet = image_packet(Image.fromarray(np.asarray(frame, dtype=np.uint8)), seq)
                packet = FramePacket(replace(packet.header, heading_deg=heading), packet.jpeg)
                yield seq / args.fps, packet
                seq += 1
                count += 1
            if not count:
                raise ValueError("Synthetic feed returned no frames")
            if not args.loop:
                return
    else:
        import cv2

        capture = cv2.VideoCapture(str(args.video) if args.video else args.webcam)
        if not capture.isOpened():
            raise ValueError("Unable to open the requested camera/video")
        seq = 0
        try:
            while True:
                ok, bgr = capture.read()
                if not ok:
                    if args.video and args.loop and seq:
                        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ok, bgr = capture.read()
                    if not ok:
                        return
                packet = image_packet(Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)), seq)
                yield seq / args.fps, packet
                seq += 1
        finally:
            capture.release()


def percentile(values, n):
    return float(np.percentile(values, n)) if values else None


async def stream(args) -> dict:
    sent = {}
    rtt, server_ms, cue_times = [], [], []
    received = 0
    transmitted = 0
    first_locked = None
    mode = None
    seen_cue_ids = set()
    start = time.monotonic()
    generator = source_frames(args)
    async with connect(
        args.url, max_size=4 * 1024 * 1024, max_queue=1, open_timeout=10, close_timeout=3
    ) as websocket:

        async def send():
            nonlocal transmitted
            try:
                while True:
                    item = await asyncio.to_thread(next, generator, None)
                    if item is None:
                        break
                    offset, packet = item
                    if args.seconds and offset >= args.seconds:
                        break
                    delay = start + offset - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    if args.seconds and time.monotonic() - start >= args.seconds:
                        break
                    header = replace(packet.header, seq=transmitted, ts_ms=time.time() * 1000)
                    sent[transmitted] = time.monotonic()
                    # Bounded bookkeeping even if a server drops almost every frame.
                    while len(sent) > 512:
                        sent.pop(next(iter(sent)))
                    await websocket.send(encode_frame(header, packet.jpeg))
                    transmitted += 1
                if args.loop and args.seconds:
                    await asyncio.sleep(max(0, start + args.seconds - time.monotonic()))
            finally:
                generator.close()

        async def receive():
            nonlocal received, first_locked, mode
            async for message in websocket:
                result = json.loads(message)
                now = time.monotonic()
                stamp = sent.pop(result["seq"], None)
                if stamp is not None:
                    rtt.append((now - stamp) * 1000)
                server_ms.append(result["server_ms"]["total"])
                received += 1
                mode = result.get("mode")
                if result["location"]["state"] == "LOCKED" and first_locked is None:
                    first_locked = (now - start) * 1000
                cue = result.get("cue")
                if cue:
                    if cue["id"] in seen_cue_ids:
                        raise ValueError("Repeated cue ID")
                    seen_cue_ids.add(cue["id"])
                    # Server's monotonic result time is not on the wire. RTT jitter can
                    # distort receive gaps; cue engine timing is checked in integration tests.
                    cue_times.append(now - start)
                    if not args.quiet:
                        print(f"cue P{cue['priority']}: {cue['text']}", flush=True)

        sender = asyncio.create_task(send())
        receiver = asyncio.create_task(receive())
        try:
            done, _ = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
            if receiver in done:
                receiver.result()
                raise RuntimeError("Server disconnected before the source completed")
            sender.result()
            # Allow the last in-flight + latest pending frame to finish, with a strict limit.
            deadline = time.monotonic() + 3
            while transmitted - 1 in sent and time.monotonic() < deadline and not receiver.done():
                await asyncio.sleep(0.01)
            if receiver.done():
                receiver.result()
            end = time.monotonic()
        finally:
            sender.cancel()
            receiver.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    if not received:
        raise RuntimeError("No frame results received")
    elapsed = end - start
    return {
        "source": "replay"
        if args.replay
        else "synthetic"
        if args.synthetic
        else "images"
        if args.images
        else "video"
        if args.video
        else "webcam",
        "elapsed_s": elapsed,
        "sent": transmitted,
        "received": received,
        "fps": received / elapsed,
        "rtt_ms": {"p50": percentile(rtt, 50), "p95": percentile(rtt, 95)},
        "server_ms": {"p50": percentile(server_ms, 50), "p95": percentile(server_ms, 95)},
        "time_to_locked_ms": first_locked,
        "cues": len(cue_times),
        "mode": mode,
    }


def read_metrics(url):
    base = url.replace("wss://", "https://", 1).replace("ws://", "http://", 1).split("/ws/phone")[0]
    with urlopen(base + "/metrics", timeout=5) as response:
        return json.load(response)


def demo_failures(report, metrics, evaluation):
    """Strict full-demo gate; missing evidence is a failure, never a simulated pass."""
    failures = []
    if "frames" in evaluation:
        # Native Part 3 eval/results.json (held-out test split), preserving its extra gates.
        original = evaluation
        frame_metrics = original.get("frames", {})
        geometry = frame_metrics.get("geometry", {})
        walks = original.get("walkthroughs", {})
        evaluation = {
            "split": "held-out",
            "simulated": original.get("simulated"),
            "false_locked_rate": geometry.get("false_match_rate"),
            "recall": geometry.get("recall"),
            "negative_walkthroughs": walks.get("neg_walkthroughs"),
            "false_locked_walkthroughs": walks.get("neg_false_locked"),
            "locked_within_2s_trials": walks.get("pos_locked_within_2s"),
            "yaw_median_deg": frame_metrics.get("yaw_err_median"),
            "yaw_p95_deg": frame_metrics.get("yaw_err_p95"),
        }
        targets = original.get("targets", [])
        if not targets or not all(t.get("pass") is True for t in targets):
            failures.append("Part 3 evaluation targets did not all pass")
    if report["mode"] != {"detector": "yolo", "localizer": "real"}:
        failures.append("requires YOLO and the real localizer")
    checks = [
        (report.get("fps"), 4, ">=", "detection throughput >= 4 fps"),
        (report.get("rtt_ms", {}).get("p95"), 300, "<", "p95 round trip < 300 ms"),
        (report.get("time_to_locked_ms"), 2000, "<", "time to LOCKED < 2 s"),
        (metrics.get("capture_to_audible_ms", {}).get("p50"), 500, "<", "audible p50 < 500 ms"),
        (metrics.get("capture_to_audible_ms", {}).get("p95"), 900, "<", "audible p95 < 900 ms"),
        (evaluation.get("false_locked_rate"), 0.01, "<", "held-out false LOCKED < 1%"),
        (evaluation.get("recall"), 0.9, ">=", "held-out recall >= 90%"),
        (evaluation.get("negative_walkthroughs"), 20, ">=", "20 negative walkthroughs"),
        (evaluation.get("false_locked_walkthroughs"), 0, "<=", "zero false LOCKED walkthroughs"),
        (evaluation.get("locked_within_2s_trials"), 20, ">=", "20 successful lock trials"),
        (evaluation.get("yaw_median_deg"), 8, "<", "median yaw error < 8 degrees"),
        (evaluation.get("yaw_p95_deg"), 20, "<", "p95 yaw error < 20 degrees"),
    ]
    for value, limit, operator, name in checks:
        ok = type(value) in (int, float) and math.isfinite(value)
        if ok:
            ok = (
                value < limit
                if operator == "<"
                else value <= limit
                if operator == "<="
                else value >= limit
            )
        if not ok:
            failures.append(name + " (missing or outside target)")
    if evaluation.get("split") != "held-out" or evaluation.get("simulated") is not True:
        failures.append("localization report must identify held-out simulated evaluation")
    return failures


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/phone")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--images", type=Path)
    source.add_argument("--video", type=Path)
    source.add_argument("--webcam", type=int)
    source.add_argument("--replay", type=Path)
    source.add_argument("--synthetic", action="store_true")
    parser.add_argument("--fps", type=float, default=6)
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--demo-check", action="store_true")
    parser.add_argument("--eval-report", type=Path, default=Path("data/reports/localization.json"))
    parser.add_argument("--soak", action="store_true")
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args(argv)
    if not math.isfinite(args.fps) or not 0 < args.fps <= 60:
        parser.error("--fps must be in (0, 60]")
    if not math.isfinite(args.seconds) or args.seconds < 0 or args.runs < 1:
        parser.error("--seconds must be nonnegative and --runs must be positive")
    if args.soak:
        if not args.replay:
            parser.error("--soak requires --replay SESSION")
        args.loop = True
        args.seconds = args.seconds or 180
    if args.demo_check:
        args.seconds = args.seconds or 60
        args.loop = True
        if not any((args.images, args.video, args.webcam is not None, args.replay)):
            args.synthetic = True
    if not any((args.images, args.video, args.webcam is not None, args.replay, args.synthetic)):
        parser.error("Choose --images, --video, --webcam, --replay or --synthetic")
    return args


async def main(argv=None):
    args = parse_args(argv)
    reports = []
    for index in range(args.runs if args.soak else 1):
        before = await asyncio.to_thread(read_metrics, args.url)
        report = await stream(args)
        after = await asyncio.to_thread(read_metrics, args.url)
        if after["errors"] != before["errors"] or after["invalid"] != before["invalid"]:
            raise RuntimeError("Server reported pipeline errors or invalid frames")
        if args.soak and report["elapsed_s"] < args.seconds - 0.1:
            raise RuntimeError("Soak run ended before its requested duration")
        report["run"] = index + 1
        reports.append(report)
        print(json.dumps(report, indent=2), flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(reports, indent=2) + "\n")
        if args.demo_check:
            eval_path = args.eval_report
            if not eval_path.is_file() and eval_path == Path("data/reports/localization.json"):
                eval_path = Path("data/eval/results.json")
            evaluation = json.loads(eval_path.read_text()) if eval_path.is_file() else {}
            failures = demo_failures(report, after, evaluation)
            if failures:
                for failure in failures:
                    print(f"FAIL {failure}", file=sys.stderr)
                return 1
            print("PASS full demo check (localization accuracy is simulated)")
    if args.soak:
        print(f"PASS {len(reports)} x {args.seconds:g}s replay runs; no server errors", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        raise SystemExit(1) from None
