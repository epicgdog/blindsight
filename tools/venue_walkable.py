"""Where a person can stand on each venue level: a mask in plan pixels, server/venue/walk_<level>.png.

The live position is snapped onto it (server/venue.py), so the dot never sits in a hole, a wall
or an office. Built from:

1. The plan: white corridors and coloured rooms (atrium green, hacker space yellow, sponsor blue,
   judges teal, judging pink); grey offices and walls are out. Small gaps from labels and
   column marks are closed.
2. The 3D scan, where there is one: inside the scanned area (Levels 1 and 2, JRR atrium and ring
   corridors) a pixel must also have scan floor at that level. This removes the big hole of the
   Level 2 atrium, which the plan draws as white with an X.
3. Holes outside the scan, traced by hand from the X marks and "open to below" labels.
4. Only areas connected to the traced walk graph are kept (drops the street, legend, specks).

  uv run python3 tools/venue_walkable.py [--overlay DIR]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from tools.venue_align import mp_floor_points, to_img, walkable

ROOT = Path(__file__).resolve().parents[1]
VENUE = ROOT / "server" / "venue"

# venue level -> Matterport floor of the scan (as in tools/venue_align.py); Level A has no scan
SCAN_FLOOR = {"1": 2, "2": 3}
# Holes drawn on the plans (X or "open to below") outside the scan, plan pixels
VOIDS = {
    "1": [[(838, 382), (868, 382), (868, 416), (838, 416)]],  # LAS, "open to below"
    "2": [[(652, 562), (748, 562), (748, 668), (674, 668), (674, 646), (652, 646)]],  # LAS atrium, X
    # JRR west areaway and the terrace south of it: outlines on the plan, not rooms or corridors
    "A": [[(198, 300), (272, 300), (272, 712), (198, 712)], [(198, 712), (398, 712), (398, 792), (198, 792)]],
}


def coloured(img: Image.Image) -> np.ndarray:
    """Event room fills: saturated colour (yellow, blue, teal, pink, green)."""
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    hi, lo = a.max(axis=2), a.min(axis=2)
    return (hi - lo > 60) & (hi > 150)


def footprint(img: Image.Image, gap_px: int = 21) -> np.ndarray:
    """Inside of the buildings: drawn (non-white) pixels, closed over door-sized gaps in the outer
    walls, with every enclosed area filled. The street and page background stay outside."""
    a = np.asarray(img.convert("RGB"))
    drawn = (~((a[..., 0] > 235) & (a[..., 1] > 235) & (a[..., 2] > 235))).astype(np.uint8)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gap_px, gap_px))
    closed = cv2.morphologyEx(drawn, cv2.MORPH_CLOSE, k)
    h, w = closed.shape
    outside = closed.copy()
    flood = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(outside, flood, (0, 0), 2)  # pad is white page: (0, 0) is outside
    return (outside != 2).astype(np.uint8)


def scan_floor(level: str, align: dict, shape) -> np.ndarray | None:
    if level not in SCAN_FLOOR:
        return None
    uv = np.round(to_img(mp_floor_points(SCAN_FLOOR[level], 0.1), align[level])).astype(int)
    h, w = shape
    ok = (uv[:, 0] >= 0) & (uv[:, 0] < w) & (uv[:, 1] >= 0) & (uv[:, 1] < h)
    m = np.zeros(shape, np.uint8)
    m[uv[ok, 1], uv[ok, 0]] = 1
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))


def build(level: str, venue: dict, align: dict) -> tuple[np.ndarray, dict]:
    img = Image.open(VENUE / f"level_{level}.png")
    plan = (walkable(img) | coloured(img)).astype(np.uint8)
    plan = cv2.morphologyEx(plan, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))  # labels, column marks
    plan &= footprint(img)
    walk = plan.copy()
    info: dict = {}
    scan = scan_floor(level, align, plan.shape)
    if scan is not None:
        hull = np.zeros_like(plan)
        ys, xs = np.nonzero(scan)
        cv2.fillPoly(hull, [cv2.convexHull(np.stack([xs, ys], axis=1).astype(np.int32))], 1)
        near_scan = cv2.dilate(scan, np.ones((7, 7), np.uint8))  # about 0.5 m of slack
        walk = np.where(hull > 0, plan & near_scan, plan)
        info["scanned_px"] = int(hull.sum())
        info["removed_by_scan_px"] = int((plan & hull & (1 - near_scan)).sum())
    for poly in VOIDS.get(level, []):
        cv2.fillPoly(walk, [np.array(poly, np.int32)], 0)
    # keep the parts the walk graph touches; strips under about 0.7 m wide (the dashed building divider,
    # gaps between outline lines) are dropped by testing on an opened copy
    k = np.ones((5, 5), np.uint8)
    _, lab = cv2.connectedComponents(cv2.morphologyEx(walk, cv2.MORPH_OPEN, k), connectivity=8)
    rooms = [r for r in venue["rooms"] if r["level"] == level]
    nodes = venue["nodes"][level]
    seeds = list(nodes.values()) + [r["door"] for r in rooms] + [r["label"] for r in rooms]
    for chain in venue["corridors"].get(level, []):  # along each traced corridor, every 4 px
        for a, b in zip(chain, chain[1:]):
            (ua, va), (ub, vb) = nodes[a], nodes[b]
            n = max(1, int(np.hypot(ub - ua, vb - va) // 4))
            seeds += [(ua + (ub - ua) * i / n, va + (vb - va) * i / n) for i in range(n + 1)]
    keep = set()
    for u, v in seeds:
        u, v = int(round(u)), int(round(v))
        win = lab[max(0, v - 4):v + 5, max(0, u - 4):u + 5]
        keep.update(int(x) for x in np.unique(win) if x)
    walk = walk & cv2.dilate(np.isin(lab, list(keep)).astype(np.uint8), k)
    off = [k for k, (u, v) in venue["nodes"][level].items()
           if not walk[max(0, int(v) - 3):int(v) + 4, max(0, int(u) - 3):int(u) + 4].any()]
    info.update(walkable_px=int(walk.sum()), nodes_off_mask=off)
    return walk, info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--overlay", help="directory for check images (mask in red over the plan)")
    a = ap.parse_args()
    venue = json.loads((VENUE / "venue.json").read_text())
    align = json.loads((VENUE / "align.json").read_text())
    for lv in venue["levels"]:
        level = lv["id"]
        walk, info = build(level, venue, align)
        Image.fromarray(walk * 255).convert("1").save(VENUE / f"walk_{level}.png", optimize=True)
        print(f"level {level}: {info}")
        if a.overlay:
            rgb = np.asarray(Image.open(VENUE / f"level_{level}.png").convert("RGB")).copy()
            m = walk > 0
            rgb[m] = (0.55 * rgb[m] + 0.45 * np.array([230, 30, 30])).astype(np.uint8)
            for u, v in venue["nodes"][level].values():
                cv2.circle(rgb, (int(u), int(v)), 3, (0, 90, 255), -1)
            Image.fromarray(rgb).save(Path(a.overlay) / f"walk_{level}.png")


if __name__ == "__main__":
    main()
