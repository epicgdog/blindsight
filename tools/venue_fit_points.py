"""Fit a venue level to the map frame from scan points: every scan point must stand on a
walkable pixel (white corridor or green atrium) of the venue plan. Scale is searched in a
narrow band (from Level 1's corridor fit), rotation fixed at 0 (both plans are north up).

  uv run python3 tools/venue_fit_points.py --level 2 --floor 2 --scale 7.0,8.2 --region 300,330,620,560
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
import cv2

from tools.venue_align import walkable

ROOT = Path(__file__).resolve().parents[1]
VENUE = ROOT / "server" / "venue"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", required=True)
    ap.add_argument("--floor", type=int, required=True, help="our floor index of the scan points (0 or 2)")
    ap.add_argument("--scale", default="7.0,8.2")
    ap.add_argument("--region", default="300,330,640,560")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    from server.minimap import scan_points
    pts = np.array([(p["x"], p["y"]) for p in scan_points(ROOT / "data")["points"] if p["floor"] == a.floor])
    img = Image.open(VENUE / f"level_{a.level}.png")
    # a point must sit inside a corridor, not on its edge
    walk = cv2.erode(walkable(img).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    h, w = walk.shape
    smin, smax = (float(v) for v in a.scale.split(","))
    x0, y0, x1, y1 = (int(v) for v in a.region.split(","))
    cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
    best = []
    for s in np.arange(smin, smax + 1e-6, 0.05):
        for u in np.arange(x0, x1, 1.0):
            for v in np.arange(y0, y1, 1.0):
                tx, ty = u - s * cx, v + s * cy
                uu = np.round(s * pts[:, 0] + tx).astype(int)
                vv = np.round(-s * pts[:, 1] + ty).astype(int)
                if uu.min() < 0 or vv.min() < 0 or uu.max() >= w or vv.max() >= h:
                    continue
                best.append((int(walk[vv, uu].sum()), float(s), float(tx), float(ty)))
    best.sort(reverse=True)
    top = best[0][0]
    ties = [b for b in best if b[0] == top]
    # Among equally good fits, take the centre of the tie set: the middle of the feasible band.
    s = float(np.median([b[1] for b in ties]))
    near = [b for b in ties if abs(b[1] - s) < 0.03] or ties
    tx = float(np.median([b[2] for b in near])); ty = float(np.median([b[3] for b in near]))
    print(f"level {a.level}: {top}/{len(pts)} points on walkable; {len(ties)} tied fits; scale {s:.3f} tx {tx:.1f} ty {ty:.1f}")
    im = img.convert("RGB").copy(); d = ImageDraw.Draw(im)
    for x, y in pts:
        U, V = s * x + tx, -s * y + ty
        d.ellipse((U - 3, V - 3, U + 3, V + 3), fill=(220, 0, 0))
    im.save(VENUE / f"align_check_{a.level}.png")
    if a.write:
        path = VENUE / "align.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        data[a.level] = {"scale": round(s, 4), "rot": 0.0, "tx": round(tx, 2), "ty": round(ty, 2),
                         "points_on_walkable": f"{top}/{len(pts)}", "method": "scan points on walkable pixels"}
        path.write_text(json.dumps(data, indent=1))


if __name__ == "__main__":
    main()
