"""Procedural test room for Part 3 tests (NOT the lobby, never used for pitch numbers).

A textured box room, ray-cast from any position, so tests can render skybox faces
for several scan points and perspective views with exact ground-truth pose. Walls
are planar, which makes views of the same wall from another scan point a genuine
hard negative: they fit a homography but not a pure rotation.
"""

from __future__ import annotations

import json
import string
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
from geometry import cam_to_world, pixel_dirs_cam  # noqa: E402
from refs import CubeLayout, remap_flat  # noqa: E402

LO = np.array([-8.0, 0.0, -6.0])
HI = np.array([8.0, 4.0, 6.0])
EYE = 1.5  # camera height, metres

# Scan points (x, z) in metres. p0 is the "demo" pano; the others are 2.5-7 m away.
POSITIONS = {
    "room_p0": (0.0, 0.0),
    "room_p1": (2.5, 0.5),
    "room_p2": (-3.0, 1.0),
    "room_p3": (0.5, 3.5),
    "room_p4": (4.0, -3.0),
    "room_p5": (-5.0, -3.5),
    "room_p6": (6.0, 3.0),
}


def _texture(w: int, h: int, seed: int, kind: str) -> np.ndarray:
    rng = np.random.default_rng(seed)
    base = rng.integers(60, 200, 3)
    img = np.empty((h, w, 3), np.uint8)
    img[:] = base
    noise = cv2.resize(rng.normal(0, 18, (max(2, h // 16), max(2, w // 16), 3)).astype(np.float32), (w, h))
    img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    if kind == "tiles":  # repetitive floor
        step = 50
        for y in range(0, h, step):
            for x in range(0, w, step):
                c = (base + rng.integers(-25, 25, 3)).clip(0, 255).tolist()
                cv2.rectangle(img, (x + 2, y + 2), (x + step - 2, y + step - 2), c, -1)
        return img
    if kind == "panels":  # repetitive ceiling lights
        for y in range(40, h, 120):
            for x in range(40, w, 160):
                cv2.rectangle(img, (x, y), (x + 90, y + 40), (235, 235, 225), -1)
        return img
    n_big, n_small = (w * h) // 9000, (w * h) // 1200
    for i in range(n_big + n_small):
        big = i < n_big
        c = rng.integers(0, 256, 3).tolist()
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        s = int(rng.integers(30, 160) if big else rng.integers(6, 30))
        r = rng.random()
        if r < 0.35:
            cv2.rectangle(img, (x, y), (x + s, y + int(s * rng.uniform(0.4, 1.6))), c, -1 if rng.random() < 0.7 else 2)
        elif r < 0.6:
            cv2.circle(img, (x, y), max(2, s // 2), c, -1 if rng.random() < 0.6 else 2)
        elif r < 0.8:
            pts = (np.array([x, y]) + rng.integers(-s, s, (int(rng.integers(3, 6)), 2))).astype(np.int32)
            cv2.fillPoly(img, [pts], c)
        else:
            txt = "".join(rng.choice(list(string.ascii_uppercase + string.digits), int(rng.integers(2, 7))))
            cv2.putText(img, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, s / 40.0, c, max(1, s // 15), cv2.LINE_AA)
    return img


@dataclass
class Room:
    seed: int = 0
    ppm: int = 100  # texture pixels per metre

    def __post_init__(self):
        dx, dy, dz = (HI - LO)
        p = self.ppm
        # surfaces keyed by (axis, side): side +1 = HI plane, -1 = LO plane
        self.tex = {
            (0, -1): _texture(int(dz * p), int(dy * p), self.seed * 10 + 1, "wall"),
            (0, +1): _texture(int(dz * p), int(dy * p), self.seed * 10 + 2, "wall"),
            (2, -1): _texture(int(dx * p), int(dy * p), self.seed * 10 + 3, "wall"),
            (2, +1): _texture(int(dx * p), int(dy * p), self.seed * 10 + 4, "wall"),
            (1, -1): _texture(int(dx * p), int(dz * p), self.seed * 10 + 5, "tiles"),
            (1, +1): _texture(int(dx * p), int(dz * p), self.seed * 10 + 6, "panels"),
        }

    def trace(self, origin: np.ndarray, dirs: np.ndarray) -> np.ndarray:
        shp = dirs.shape[:-1]
        d = dirs.reshape(-1, 3)
        with np.errstate(divide="ignore", invalid="ignore"):
            t_hi = (HI - origin) / d
            t_lo = (LO - origin) / d
        t_exit = np.where(d > 0, t_hi, np.where(d < 0, t_lo, np.inf))
        axis = np.argmin(t_exit, axis=1)
        t = t_exit[np.arange(len(d)), axis]
        hit = origin + t[:, None] * d
        side = np.sign(d[np.arange(len(d)), axis]).astype(int)
        out = np.zeros((len(d), 3), np.uint8)
        for (ax, sd), tex in self.tex.items():
            m = (axis == ax) & (side == sd)
            if not m.any():
                continue
            th, tw = tex.shape[:2]
            hp = hit[m]
            if ax == 1:  # floor / ceiling: (x, z)
                u = (hp[:, 0] - LO[0]) / (HI[0] - LO[0])
                v = (hp[:, 2] - LO[2]) / (HI[2] - LO[2])
            else:  # walls: horizontal coord, then height (top of texture = ceiling)
                hc = 2 if ax == 0 else 0
                u = (hp[:, hc] - LO[hc]) / (HI[hc] - LO[hc])
                if sd < 0:
                    u = 1.0 - u
                v = (HI[1] - hp[:, 1]) / (HI[1] - LO[1])
            mx = np.clip(u * tw - 0.5, 0, tw - 1).astype(np.float32)
            my = np.clip(v * th - 0.5, 0, th - 1).astype(np.float32)
            out[m] = remap_flat(tex, mx, my)
        return out.reshape(*shp, 3)

    def render(self, pos, yaw, pitch=0.0, roll=0.0, size=(360, 480), hfov=55.0) -> np.ndarray:
        w, h = size
        dirs = pixel_dirs_cam(w, h, hfov) @ cam_to_world(yaw, pitch, roll).T
        return self.trace(np.asarray(pos, float), dirs)

    def faces(self, pos, S: int = 384, layout: CubeLayout | None = None) -> list[np.ndarray]:
        layout = layout or CubeLayout()
        cam = pixel_dirs_cam(S, S, 90.0)
        return [self.trace(np.asarray(pos, float), cam @ layout.rotation(k).T) for k in range(6)]


def pos3(pano_id: str) -> np.ndarray:
    x, z = POSITIONS[pano_id]
    return np.array([x, EYE, z])


def write_dataset(root: Path, room: Room | None = None, S: int = 384, demo: str = "room_p0",
                  ids: list[str] | None = None) -> Path:
    """Write a data/ tree like scripts/fetch_matterport.py produces, for the room."""
    room = room or Room()
    root = Path(root)
    ids = ids or list(POSITIONS)
    for pid in ids:
        d = root / "pano" / pid
        d.mkdir(parents=True, exist_ok=True)
        for k, f in enumerate(room.faces(pos3(pid), S)):
            cv2.imwrite(str(d / f"face_{k}.jpg"), cv2.cvtColor(f, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 93])
    p0 = pos3(demo)
    meta = {
        "model_id": "procedural-test-room",
        "pano_id": demo,
        "label": "test room",
        "position": {"x": p0[0], "y": p0[2], "z": p0[1]},
        "selected_by": "tests/p3_room.py (procedural fixture, not Matterport data)",
        "negatives": [
            {"pano_id": pid, "label": "test room",
             "position": {"x": pos3(pid)[0], "y": pos3(pid)[2], "z": pos3(pid)[1]},
             "dist_m": round(float(np.linalg.norm(pos3(pid) - p0)), 2)}
            for pid in ids if pid != demo
        ],
    }
    (root / "pano" / "pano.json").write_text(json.dumps(meta, indent=1))
    return root
