"""Fit a venue floor plan image (HackPrinceton map) to the Matterport map frame.

The scan's walkable floor (Matterport floor plan alpha mask: where the scan has floor) must land
on walkable pixels of the venue plan: white corridors and the green atrium. Brute force over
scale and shift (rotation searched in small steps), coarse to fine; writes the transform
image px -> map metres into server/venue/align.json and an overlay PNG for a visual check.

  uv run python3 tools/venue_align.py --level 1 --mp-floor 2
  uv run python3 tools/venue_align.py --level 2 --mp-floor 3
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
VENUE = ROOT / "server" / "venue"
MP = ROOT / "data" / "matterport" / "floorplans"


def walkable(img: Image.Image) -> np.ndarray:
    """Venue plan pixels a person can stand on: near-white (corridors, open atrium) or the
    green atrium highlight. Grey rooms, walls, yellow/blue room fills are not walkable."""
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    white = (r > 235) & (g > 235) & (b > 235)
    green = (g > 170) & (r < 190) & (b < 160) & (g - r > 30)
    return white | green


def mp_floor_points(mp_floor: int, step_m: float = 0.25) -> np.ndarray:
    """Map-frame points (metres) where the Matterport scan has floor, on a step_m grid."""
    meta = json.loads((MP / "index.json").read_text())
    m = next(x for x in meta if x["floor"] == mp_floor - 1 and "alpha" in x["file"] and x["width"] >= 2000)
    a = np.asarray(Image.open(MP / m["file"]).convert("RGBA"))[..., 3] > 0
    s, (ox, oy), h = m["px_per_m"], (m["origin"]["x"], m["origin"]["y"]), a.shape[0]
    stride = max(1, int(round(step_m * s)))
    ys, xs = np.nonzero(a[::stride, ::stride])
    px, py = xs * stride, ys * stride
    return np.stack([ox + px / s, oy + (h - py) / s], axis=1)


def to_img(pts: np.ndarray, t: dict) -> np.ndarray:
    """Map metres -> venue image px for transform t (scale px/m, rotation deg, tx, ty).
    Image y grows down, map y grows north (up)."""
    c, s = math.cos(math.radians(t["rot"])), math.sin(math.radians(t["rot"]))
    x, y = pts[:, 0], pts[:, 1]
    u = t["scale"] * (c * x - s * y) + t["tx"]
    v = -t["scale"] * (s * x + c * y) + t["ty"]
    return np.stack([u, v], axis=1)


def score(walk: np.ndarray, pts: np.ndarray, t: dict) -> float:
    uv = np.round(to_img(pts, t)).astype(int)
    h, w = walk.shape
    ok = (uv[:, 0] >= 0) & (uv[:, 0] < w) & (uv[:, 1] >= 0) & (uv[:, 1] < h)
    if ok.mean() < 0.98:
        return 0.0
    return float(walk[uv[ok, 1], uv[ok, 0]].mean())


def fit(walk: np.ndarray, pts: np.ndarray, region: tuple[int, int, int, int], smin: float, smax: float) -> dict:
    x0, y0, x1, y1 = region
    best = (0.0, None)
    # coarse: scale smin..smax px/m (from room sizes; free scale shrinks the scan into one open area)
    cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
    sub = pts[:: max(1, len(pts) // 1500)]
    for rot in (-2.0, -1.0, 0.0, 1.0, 2.0):
        for scale in np.arange(smin, smax + 1e-6, 0.1):
            for u in range(x0, x1, 4):
                for v in range(y0, y1, 4):
                    c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
                    t = {"scale": scale, "rot": rot,
                         "tx": u - scale * (c * cx - s * cy), "ty": v + scale * (s * cx + c * cy)}
                    sc = score(walk, sub, t)
                    if sc > best[0]:
                        best = (sc, t)
    t = dict(best[1])
    # fine: local search on all points
    steps = {"scale": 0.05, "rot": 0.25, "tx": 1.0, "ty": 1.0}
    cur = score(walk, pts, t)
    for _ in range(4):
        improved = True
        while improved:
            improved = False
            for k, d in steps.items():
                for sgn in (-1, 1):
                    cand = dict(t)
                    cand[k] += sgn * d
                    if not smin <= cand["scale"] <= smax:
                        continue
                    sc = score(walk, pts, cand)
                    if sc > cur + 1e-6:
                        t, cur, improved = cand, sc, True
        steps = {k: v / 2 for k, v in steps.items()}
    t["score"] = round(cur, 4)
    return t


def overlay(img: Image.Image, pts: np.ndarray, t: dict, scan: list[tuple[float, float]], out: Path) -> None:
    im = img.convert("RGB").copy()
    d = ImageDraw.Draw(im, "RGBA")
    for u, v in to_img(pts[:: max(1, len(pts) // 4000)], t):
        d.point((u, v), fill=(255, 0, 255, 120))
    for u, v in to_img(np.array(scan), t):
        d.ellipse((u - 3, v - 3, u + 3, v + 3), fill=(220, 0, 0, 255))
    im.save(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", required=True, help="venue level id: 1, 2, A")
    ap.add_argument("--mp-floor", type=int, required=True, help="Matterport floor number (1-3)")
    ap.add_argument("--region", default="380,330,640,540", help="x0,y0,x1,y1 where the scan centre may land")
    ap.add_argument("--scale", default="6.5,8.0", help="min,max px per metre")
    a = ap.parse_args()
    img = Image.open(VENUE / f"level_{a.level}.png")
    walk = walkable(img)
    pts = mp_floor_points(a.mp_floor)
    smin, smax = (float(v) for v in a.scale.split(","))
    t = fit(walk, pts, tuple(int(v) for v in a.region.split(",")), smin, smax)
    from server.minimap import scan_points
    sp = scan_points(ROOT / "data")
    want = {2: 0, 3: 2}[a.mp_floor]
    scan = [(p["x"], p["y"]) for p in sp["points"] if p["floor"] == want]
    overlay(img, pts, t, scan, VENUE / f"align_check_{a.level}.png")
    path = VENUE / "align.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[a.level] = {k: round(float(v), 4) for k, v in t.items()}
    path.write_text(json.dumps(data, indent=1))
    print(a.level, data[a.level])


if __name__ == "__main__":
    main()
