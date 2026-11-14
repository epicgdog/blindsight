"""Part 3 synthetic feed: a virtual phone camera panning across the pano.

Lets the whole pipeline run with no phone and no lobby:
  * Part 1's sim client can stream the frame directory (image-dir mode) or import
    iter_feed() directly; frames.jsonl carries the simulated compass heading.
  * Part 2's backup video renderer calls frames(n) (JPEG bytes, w, h) or uses --video.
  * make_synthetic.py and eval_localization.py use render_pan() for walkthroughs.

Ground truth (yaw, pitch) is written next to every frame. Headings simulate the
phone compass: true yaw plus a constant offset (compass north is not the pano's
yaw 0) plus noise.

  python tools/synthetic_feed.py --out data/synthetic/feed --seconds 36 --fps 10
  python tools/synthetic_feed.py --video data/synthetic/feed.mp4 --seconds 20
  python tools/synthetic_feed.py --pano-id <other id> ...   # a negative walkthrough
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from refs import DATA_DIR, CubeSampler, load_faces, load_layout, load_pano_meta  # noqa: E402

DEFAULT_SIZE = (720, 960)  # what a portrait phone frame might look like before the server resizes it


@dataclass
class FeedFrame:
    i: int
    t: float  # seconds since start
    frame_rgb: np.ndarray
    heading_deg: float | None
    yaw: float  # ground truth, pano frame
    pitch: float
    roll: float


def photometric(img: np.ndarray, rng: np.random.Generator, strength: float = 1.0) -> np.ndarray:
    """Lighting / sensor augmentation: brightness, contrast, gamma, blur, noise, JPEG."""
    x = img.astype(np.float32) / 255.0
    x = x * rng.uniform(1 - 0.25 * strength, 1 + 0.25 * strength)  # brightness
    m = x.mean()
    x = (x - m) * rng.uniform(1 - 0.3 * strength, 1 + 0.3 * strength) + m  # contrast
    x = np.clip(x, 0, 1) ** rng.uniform(1 - 0.3 * strength, 1 + 0.4 * strength)  # gamma
    out = (x * 255).astype(np.uint8)
    r = rng.random()
    if r < 0.4 * strength:
        out = cv2.GaussianBlur(out, (0, 0), rng.uniform(0.3, 1.5))
    elif r < 0.55 * strength:  # motion blur
        k = int(rng.integers(3, 8))
        ker = np.zeros((k, k), np.float32)
        ker[k // 2, :] = 1.0 / k
        M = cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), float(rng.uniform(0, 180)), 1.0)
        out = cv2.filter2D(out, -1, cv2.warpAffine(ker, M, (k, k)))
    sigma = rng.uniform(0, 8 * strength)
    if sigma > 0.5:
        out = np.clip(out.astype(np.float32) + rng.normal(0, sigma, out.shape), 0, 255).astype(np.uint8)
    q = int(rng.integers(int(40 + 50 * (1 - strength)), 96))
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(out, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
    return cv2.cvtColor(cv2.imdecode(buf, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def load_sampler(data_dir: Path = DATA_DIR, pano_id: str | None = None) -> tuple[CubeSampler, str]:
    pid = pano_id or load_pano_meta(data_dir)["pano_id"]
    pdir = Path(data_dir) / "pano" / pid
    return CubeSampler(load_faces(pdir), load_layout(pdir)), pid


def render_pan(sampler: CubeSampler, n_frames: int, fps: float = 10.0, start_yaw: float = 0.0,
               yaw_speed_dps: float = 30.0, pitch_amp: float = 4.0, roll_amp: float = 1.5,
               hfov: float = 55.0, size: tuple[int, int] = DEFAULT_SIZE, heading_offset: float = 0.0,
               heading_noise: float = 2.0, heading: bool = True, augment: float = 0.0,
               seed: int = 0, sweep: float | None = None) -> Iterator[FeedFrame]:
    """Virtual camera at the scan point. yaw advances at yaw_speed_dps; with sweep set
    the camera pans back and forth over +-sweep/2 degrees instead of a full circle."""
    rng = np.random.default_rng(seed)
    for i in range(n_frames):
        t = i / fps
        if sweep:
            yaw = start_yaw + (sweep / 2) * math.sin(2 * math.pi * t * yaw_speed_dps / (2 * sweep))
        else:
            yaw = start_yaw + yaw_speed_dps * t
        pitch = pitch_amp * math.sin(2 * math.pi * t / 4.0)
        roll = roll_amp * math.sin(2 * math.pi * t / 3.1)
        img = sampler.render(yaw, pitch, roll, size, hfov)
        if augment > 0:
            img = photometric(img, rng, augment)
        hd = (yaw + heading_offset + rng.normal(0, heading_noise)) % 360 if heading else None
        yield FeedFrame(i, t, img, hd, yaw % 360, pitch, roll)


def iter_feed(data_dir: Path = DATA_DIR, pano_id: str | None = None, fps: float = 10.0,
              seconds: float = 36.0, **kw) -> Iterator[FeedFrame]:
    """Hook for Part 1's sim client: `for f in iter_feed(): send(f.frame_rgb, f.heading_deg)`."""
    sampler, _ = load_sampler(data_dir, pano_id)
    yield from render_pan(sampler, int(round(seconds * fps)), fps, **kw)


def frames(n: int, size: tuple[int, int] = (480, 640), data_dir: Path = DATA_DIR,
           pano_id: str | None = None):
    """Hook for Part 2's backup video renderer: n JPEG frames (bytes, w, h), one full
    slow turn at the locked pano. Raises FileNotFoundError right away (not on the first
    frame) when the pano data is missing, so the caller can fall back cleanly."""
    sampler, _ = load_sampler(data_dir, pano_id)
    w, h = size
    fps = 10.0
    speed = 360.0 * fps / max(int(n), 1)

    def gen():
        for f in render_pan(sampler, int(n), fps, 0.0, speed, hfov=55.0, size=(w, h), heading=False):
            ok, buf = cv2.imencode(".jpg", cv2.cvtColor(f.frame_rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
            yield buf.tobytes(), w, h

    return gen()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--pano-id", default=None, help="default: the locked pano from data/pano/pano.json")
    ap.add_argument("--out", type=Path, default=None, help="directory for frame_00000.jpg + frames.jsonl")
    ap.add_argument("--video", type=Path, default=None, help="also write an mp4")
    ap.add_argument("--fps", type=float, default=10.0)
    ap.add_argument("--seconds", type=float, default=36.0, help="36 s at 30 deg/s = one full turn")
    ap.add_argument("--start-yaw", type=float, default=0.0)
    ap.add_argument("--speed", type=float, default=30.0, help="pan speed, degrees per second")
    ap.add_argument("--sweep", type=float, default=None, help="pan back and forth over this many degrees")
    ap.add_argument("--size", default=f"{DEFAULT_SIZE[0]}x{DEFAULT_SIZE[1]}")
    ap.add_argument("--hfov", type=float, default=55.0)
    ap.add_argument("--heading-offset", type=float, default=0.0)
    ap.add_argument("--no-heading", action="store_true", help="send heading_deg = null")
    ap.add_argument("--augment", type=float, default=0.3, help="photometric augmentation strength 0..1")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    if a.out is None and a.video is None:
        a.out = a.data_dir / "synthetic" / "feed"
    w, h = (int(v) for v in a.size.lower().split("x"))
    sampler, pid = load_sampler(a.data_dir, a.pano_id)
    n = int(round(a.seconds * a.fps))
    writer = None
    if a.video:
        a.video.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(a.video), cv2.VideoWriter_fourcc(*"mp4v"), a.fps, (w, h))
    meta_f = None
    if a.out:
        a.out.mkdir(parents=True, exist_ok=True)
        meta_f = open(a.out / "frames.jsonl", "w")
    for f in render_pan(sampler, n, a.fps, a.start_yaw, a.speed, hfov=a.hfov, size=(w, h),
                        heading_offset=a.heading_offset, heading=not a.no_heading, augment=a.augment,
                        seed=a.seed, sweep=a.sweep):
        bgr = cv2.cvtColor(f.frame_rgb, cv2.COLOR_RGB2BGR)
        if meta_f:
            name = f"frame_{f.i:05d}.jpg"
            cv2.imwrite(str(a.out / name), bgr, [cv2.IMWRITE_JPEG_QUALITY, 88])
            meta_f.write(json.dumps({"file": name, "seq": f.i, "ts_ms": int(f.t * 1000), "heading_deg":
                                     None if f.heading_deg is None else round(f.heading_deg, 2),
                                     "w": w, "h": h, "pano_id": pid, "gt_yaw": round(f.yaw, 2),
                                     "gt_pitch": round(f.pitch, 2)}) + "\n")
        if writer:
            writer.write(bgr)
    if meta_f:
        meta_f.close()
        print(f"wrote {n} frames from pano {pid} to {a.out} (frames.jsonl has heading and ground truth)")
    if writer:
        writer.release()
        print(f"wrote {a.video}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
