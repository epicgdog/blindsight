"""Scan-point positions for the viewer's minimap (top-down: x, y in meters, floors split by height)."""

from __future__ import annotations

import json
from pathlib import Path

FLOOR_GAP_M = 1.0  # a jump in height larger than this starts a new floor


def _walk(value, out: list[dict]) -> None:
    if isinstance(value, dict):
        if "id" in value and isinstance(value.get("position"), dict):
            out.append(value)
        for child in value.values():
            _walk(child, out)
    elif isinstance(value, list):
        for child in value:
            _walk(child, out)


WALL_MARGIN_M = 3.0  # keep wall lines this far around the floor's scan points


def floor_walls(data_dir, points: list[dict], floor: int = 0) -> list[list[float]]:
    """Wall segments from data/matterport/walls_floor<N>.json near that floor's scan points."""
    here = [p for p in points if p.get("floor") == floor]
    if not here:
        return []
    try:
        raw = json.loads((Path(data_dir) / "matterport" / f"walls_floor{floor + 1}.json").read_text())
        segs = raw["segments"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    x0 = min(p["x"] for p in here) - WALL_MARGIN_M
    x1 = max(p["x"] for p in here) + WALL_MARGIN_M
    y0 = min(p["y"] for p in here) - WALL_MARGIN_M
    y1 = max(p["y"] for p in here) + WALL_MARGIN_M

    def inside(x, y):
        return x0 <= x <= x1 and y0 <= y <= y1

    out = []
    for seg in segs:
        try:
            ax, ay, bx, by = (float(v) for v in seg)
        except (TypeError, ValueError):
            continue
        if inside(ax, ay) or inside(bx, by):
            out.append([ax, ay, bx, by])
    return out


def scan_points(data_dir) -> dict:
    """All scan points with floor numbers and map bounds, from data/matterport/panoLocations.json."""
    data_dir = Path(data_dir)
    empty = {"points": [], "floors": [], "bounds": None, "walls": []}
    try:
        raw = json.loads((data_dir / "matterport" / "panoLocations.json").read_text())
    except (OSError, ValueError):
        return empty
    found: list[dict] = []
    _walk(raw, found)
    if not found:
        return empty

    rooms: dict[str, str] = {}
    try:
        meta = json.loads((data_dir / "pano" / "pano.json").read_text())
        if meta.get("pano_id") and meta.get("room"):
            rooms[meta["pano_id"]] = meta["room"]
        for n in meta.get("negatives") or []:
            if isinstance(n, dict) and n.get("pano_id") and n.get("room"):
                rooms[n["pano_id"]] = n["room"]
    except (OSError, ValueError):
        pass

    pts = []
    seen = set()
    for p in found:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        pos = p["position"]
        try:
            pts.append(
                {
                    "id": p["id"],
                    "label": p.get("label"),
                    "room": rooms.get(p["id"]),
                    "x": round(float(pos["x"]), 3),
                    "y": round(float(pos["y"]), 3),
                    "z": round(float(pos["z"]), 3),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not pts:
        return empty

    levels: list[list[float]] = []
    for z in sorted(p["z"] for p in pts):
        if levels and z - levels[-1][-1] <= FLOOR_GAP_M:
            levels[-1].append(z)
        else:
            levels.append([z])
    edges = [(lv[0], lv[-1]) for lv in levels]
    for p in pts:
        p["floor"] = next(i for i, (lo, hi) in enumerate(edges) if lo - 1e-9 <= p["z"] <= hi + 1e-9)
    floors = [
        {"floor": i, "z": round(sum(lv) / len(lv), 2), "count": len(lv)}
        for i, lv in enumerate(levels)
    ]
    xs, ys = [p["x"] for p in pts], [p["y"] for p in pts]
    bounds = {"min_x": min(xs), "max_x": max(xs), "min_y": min(ys), "max_y": max(ys)}
    return {"points": pts, "floors": floors, "bounds": bounds, "walls": floor_walls(data_dir, pts, 0)}
