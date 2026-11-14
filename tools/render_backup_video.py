"""Render the backup demo video: a virtual camera pans a pano, the pipeline runs,
headless Chrome captures the viewer, ffmpeg encodes it.

Uses Part 3's tools/synthetic_feed.py iter_feed() when its pano data is present, else a built-in placeholder pano.
Until Part 1's server lands this runs against app/dev/echo_server.py.
Output is labelled SIMULATED FEED. Usage:
    uv run python3 tools/render_backup_video.py --out slides/backup.mp4 [--seconds 60 --fps 5]
"""
import argparse
import base64
import importlib.util
import itertools
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[1]
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
BANNER = "SIMULATED FEED FROM THE SCAN - not a live camera"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def placeholder_frames(n: int, w: int = 240, h: int = 320):
    """Pan a virtual camera across a synthetic panorama (fallback when Part 3's feed is absent)."""
    pano = Image.new("RGB", (w * 4, h), (40, 50, 70))
    d = ImageDraw.Draw(pano)
    for i in range(0, w * 4, 60):
        d.rectangle([i, h // 2, i + 30, h - 20], fill=(180 - i % 120, 90, 60 + i % 150))
    for i in range(n):
        x = int(i / max(n - 1, 1) * (w * 3))
        buf = BytesIO()
        pano.crop((x, 0, x + w, h)).save(buf, "JPEG", quality=60)
        yield buf.getvalue(), w, h


def feed_frames(n: int, fps: int):
    """Frames from Part 3's virtual camera: tools/synthetic_feed.py iter_feed() -> (jpeg, w, h, heading)."""
    p = ROOT / "tools" / "synthetic_feed.py"
    spec = importlib.util.spec_from_file_location("synthetic_feed", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["synthetic_feed"] = mod  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(mod)
    for f in mod.iter_feed(fps=float(fps), seconds=n / fps):
        buf = BytesIO()
        Image.fromarray(f.frame_rgb).save(buf, "JPEG", quality=60)
        h, w = f.frame_rgb.shape[:2]
        yield buf.getvalue(), w, h, f.heading_deg


def get_frames(n: int, fps: int):
    """Part 3's feed when it works (pano data present), else a labelled placeholder pan."""
    if (ROOT / "tools" / "synthetic_feed.py").exists():
        try:
            it = feed_frames(n, fps)
            first = next(it)  # fail early if pano faces or cv2 are missing
            print("[render] frames: Part 3 synthetic feed (pano pan)", flush=True)
            return itertools.chain([first], it)
        except Exception as exc:  # noqa: BLE001 - any failure means use the fallback
            print(f"[render] Part 3 feed unavailable ({type(exc).__name__}: {exc}); using placeholder", flush=True)
    return ((j, w, h, None) for j, w, h in placeholder_frames(n))


class Cdp:
    def __init__(self, ws):
        self.ws, self.i = ws, 0

    def call(self, method, **params):
        self.i += 1
        self.ws.send(json.dumps({"id": self.i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv(timeout=30))
            if m.get("id") == self.i:
                return m.get("result", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--fps", type=int, default=5)
    ap.add_argument("--out", default="slides/backup.mp4")
    ap.add_argument("--server", default=None, help="ws/http base of a running server (default: start echo server)")
    a = ap.parse_args()
    print(f"[render] {BANNER}", flush=True)
    if not os.path.exists(CHROME) or not shutil.which("ffmpeg"):
        sys.exit("need headless Chrome (set CHROME) and ffmpeg")

    procs, tmp = [], Path(tempfile.mkdtemp(prefix="blindsight-video-"))
    try:
        if a.server:
            http_base = a.server.rstrip("/")
        else:
            port = free_port()
            procs.append(subprocess.Popen([sys.executable, str(ROOT / "app/dev/echo_server.py")], cwd=ROOT,
                                          env={**os.environ, "BLINDSIGHT_PORT": str(port), "BLINDSIGHT_DATA": str(tmp / "data")},
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            http_base = f"http://127.0.0.1:{port}"
            for _ in range(75):
                try:
                    httpx.get(http_base + "/metrics", timeout=0.5); break
                except Exception:
                    time.sleep(0.2)
        ws_base = http_base.replace("http", "ws", 1)

        dbg = free_port()
        procs.append(subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", f"--remote-debugging-port={dbg}",
                                       f"--user-data-dir={tmp/'chrome'}", "--window-size=1280,720", "about:blank"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        target = None
        for _ in range(75):
            try:
                target = [t for t in httpx.get(f"http://127.0.0.1:{dbg}/json").json() if t["type"] == "page"][0]; break
            except Exception:
                time.sleep(0.2)
        if not target:
            sys.exit("Chrome did not start")

        n = a.seconds * a.fps
        frames_dir = tmp / "frames"; frames_dir.mkdir()
        with connect(target["webSocketDebuggerUrl"], max_size=None) as cws, connect(ws_base + "/ws/phone") as phone:
            cdp = Cdp(cws)
            cdp.call("Page.enable")
            cdp.call("Emulation.setDeviceMetricsOverride", width=1280, height=720, deviceScaleFactor=1, mobile=False)
            cdp.call("Page.navigate", url=http_base + "/")
            time.sleep(1.5)
            cdp.call("Runtime.evaluate", expression=(
                "const b=document.createElement('div');b.textContent=%s;"
                "b.style.cssText='position:fixed;bottom:0;left:0;right:0;background:#f5a524;color:#000;"
                "font:700 24px system-ui;padding:8px;text-align:center;z-index:9';document.body.append(b)" % json.dumps(BANNER)))
            t0 = time.time()
            for i, (jpeg, w, h, heading) in enumerate(get_frames(n, a.fps)):
                hdr = json.dumps({"seq": i + 1, "ts_ms": int(time.time() * 1000), "heading_deg": heading, "w": w, "h": h}).encode()
                phone.send(struct.pack(">I", len(hdr)) + hdr + jpeg)
                phone.recv(timeout=10)
                time.sleep(0.15)  # let the viewer render the pushed result
                shot = cdp.call("Page.captureScreenshot", format="jpeg", quality=80)
                (frames_dir / f"{i:05d}.jpg").write_bytes(base64.b64decode(shot["data"]))
                time.sleep(max(0, t0 + (i + 1) / a.fps - time.time()))
        if (Image.open(frames_dir / "00000.jpg").size[0] < 100):
            sys.exit("screenshots are empty; Chrome viewport failed")
        out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(a.fps), "-i", str(frames_dir / "%05d.jpg"),
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)], check=True)
        print(f"[render] wrote {out} ({n} frames, {a.seconds}s) - {BANNER}")
    finally:
        for p in procs:
            p.terminate()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
