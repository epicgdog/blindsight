"""Wall outlines for the minimap: slice a Matterport mesh at chest height.

    uv run python -m server.walls --obj data/matterport/<MODEL>/50k_out_res_50k.obj \
        --z 1.0 --out data/matterport/walls_floor1.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def parse_obj(text: str) -> tuple[np.ndarray, np.ndarray]:
    """Vertices (n, 3) and triangle indices (m, 3) from OBJ text; polygons are fan-triangulated."""
    verts: list[list[float]] = []
    faces: list[list[int]] = []
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "v" and len(parts) >= 4:
            verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
        elif parts[0] == "f" and len(parts) >= 4:
            idx = [int(tok.split("/")[0]) - 1 for tok in parts[1:]]
            faces.extend([idx[0], idx[k], idx[k + 1]] for k in range(1, len(idx) - 1))
    return np.array(verts, dtype=float).reshape(-1, 3), np.array(faces, dtype=int).reshape(-1, 3)


def slice_mesh(verts: np.ndarray, faces: np.ndarray, z: float) -> list[tuple[float, float, float, float]]:
    """Line segments (x1, y1, x2, y2) where the horizontal plane at height z cuts the mesh."""
    if len(faces) == 0:
        return []
    tri = verts[faces]  # (m, 3, 3)
    above = tri[:, :, 2] >= z
    mixed = above.any(axis=1) & ~above.all(axis=1)
    tri, above = tri[mixed], above[mixed]
    if len(tri) == 0:
        return []
    pts = []  # for every mixed triangle, the two points where its edges cross the plane
    for a, b in ((0, 1), (1, 2), (2, 0)):
        cross = above[:, a] != above[:, b]
        za, zb = tri[:, a, 2], tri[:, b, 2]
        t = np.where(cross, (z - za) / np.where(zb == za, 1.0, zb - za), 0.0)
        pts.append((cross, tri[:, a, :2] + t[:, None] * (tri[:, b, :2] - tri[:, a, :2])))
    out = []
    for i in range(len(tri)):
        e = [p[i] for c, p in pts if c[i]]
        if len(e) == 2:
            out.append((float(e[0][0]), float(e[0][1]), float(e[1][0]), float(e[1][1])))
    return out


def wall_segments(verts, faces, z: float, min_len: float = 0.05) -> list[list[float]]:
    """Rounded wall segments in meters, with specks shorter than min_len removed."""
    segs = []
    for x1, y1, x2, y2 in slice_mesh(verts, faces, z):
        if ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 >= min_len:
            segs.append([round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)])
    return segs


def main() -> None:
    ap = argparse.ArgumentParser(description="Slice a mesh into wall lines for the minimap")
    ap.add_argument("--obj", required=True, type=Path)
    ap.add_argument("--z", type=float, default=1.0, help="slice height in meters (default 1.0)")
    ap.add_argument("--floor", type=int, default=0, help="floor index the slice belongs to")
    ap.add_argument("--out", type=Path, default=Path("data/matterport/walls_floor1.json"))
    a = ap.parse_args()
    verts, faces = parse_obj(a.obj.read_text(errors="ignore"))
    segs = wall_segments(verts, faces, a.z)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"floor": a.floor, "z": a.z, "source": a.obj.name, "segments": segs}))
    print(f"{len(segs)} wall segments at z={a.z} m -> {a.out}")


if __name__ == "__main__":
    main()
