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


def floor_plan(data_dir, floor: int) -> dict | None:
    """Floor-plan image info from data/matterport/floorplan_floor<N>.json, if its image was downloaded."""
    base = Path(data_dir) / "matterport"
    try:
        meta = json.loads((base / f"floorplan_floor{floor + 1}.json").read_text())
        if not (base / meta["file"]).is_file():
            return None
        return {
            "url": f"/api/floorplan/{floor}",
            "width": int(meta["width"]),
            "height": int(meta["height"]),
            "resolution": float(meta["resolution"]),
            "origin": {"x": float(meta["origin"]["x"]), "y": float(meta["origin"]["y"])},
            "floor": floor,
            "label": meta.get("label", f"Floor {floor + 1}"),
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


def scan_points(data_dir) -> dict:
    """All scan points with floor numbers and map bounds, from data/matterport/panoLocations.json."""
    data_dir = Path(data_dir)
    empty = {"points": [], "floors": [], "bounds": None, "plan": None}
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
    return {"points": pts, "floors": floors, "bounds": bounds, "plan": floor_plan(data_dir, 0)}
