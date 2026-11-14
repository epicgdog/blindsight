"""Fixtures: boot the Part 2 echo server on a free port for integration tests."""
import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_frame(header: dict, jpeg: bytes) -> bytes:
    """Wire format 5.1: u32 BE N, N bytes JSON header, rest JPEG."""
    h = json.dumps(header).encode()
    return struct.pack(">I", len(h)) + h + jpeg


@pytest.fixture(scope="session")
def jpeg_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    img = Image.new("RGB", (160, 120), (200, 30, 30))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=30)
    return buf.getvalue()


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    port = free_port()
    env = {**os.environ, "BLINDSIGHT_PORT": str(port),
           "BLINDSIGHT_DATA": str(tmp_path_factory.mktemp("part2-data"))}
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "app/dev/echo_server.py")],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("echo server died: " + proc.stdout.read().decode())
        try:
            httpx.get(base + "/metrics", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("echo server did not start")
    yield {"base": base, "ws": f"ws://127.0.0.1:{port}", "port": port}
    proc.terminate()
    proc.wait(timeout=5)
