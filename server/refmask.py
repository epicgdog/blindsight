"""Mask people and furniture out of the pano reference views.

The live frame already has movable objects masked (geometry.movable_boxes_px). The pano was
scanned with its own people and furniture in it, so the same masking is applied to the
reference views: keypoints inside the boxes are dropped and the boxes are blanked before the
retrieval embedding. Detection runs once per view and is cached.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

try:
    from server.geometry import movable_boxes_px
    from server.matchviz import view_rect_polygon
except ImportError:  # imported by localize.py with server/ on sys.path
    from geometry import movable_boxes_px  # type: ignore[no-redef]
    from matchviz import view_rect_polygon  # type: ignore[no-redef]

log = logging.getLogger("insight")
CACHE_VERSION = 1


def mask_key(model_id: str, conf: float) -> str:
    """Cache key for a detector setup; changes when the model or threshold changes."""
    return hashlib.sha1(f"{CACHE_VERSION}|{model_id}|{conf}".encode()).hexdigest()[:10]


def compute_masks(
    cache_dir, views, detect_fn, key: str
) -> dict[str, list[tuple[int, int, int, int]]]:
    """Movable-object pixel boxes per reference view, cached under cache_dir.

    detect_fn(image_rgb) returns detections with .cls and a normalised .bbox. A failing
    detector degrades to no masks (and is not cached, so it is retried next start).
    """
    names = [v.name for v in views]
    path = Path(cache_dir) / f"masks_{key}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if data.get("names") == names:
                return {n: [tuple(b) for b in data["masks"][n]] for n in names}
        except (OSError, ValueError, KeyError):
            pass
    out: dict[str, list[tuple[int, int, int, int]]] = {}
    failed = False
    for v in views:
        h, w = v.image.shape[:2]
        try:
            out[v.name] = movable_boxes_px(detect_fn(v.image), w, h)
        except Exception as exc:  # noqa: BLE001 - masking must never stop localization
            if not failed:
                log.warning("Reference masking unavailable (%s: %s)", type(exc).__name__, exc)
            failed = True
            out[v.name] = []
    if not failed:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"names": names, "masks": {n: [list(b) for b in out[n]] for n in names}})
            )
        except OSError:
            pass
    return out


def region_polygons(
    views, masks, size: tuple[int, int], samples: int = 4
) -> list[list[list[float]]]:
    """Masked boxes projected onto the equirectangular pano (x unwrapped, 0..1 coordinates)."""
    w, h = size
    polys = []
    for v in views:
        for x1, y1, x2, y2 in masks.get(v.name, ()):
            rect = (x1 / w, y1 / h, x2 / w, y2 / h)
            polys.append(view_rect_polygon(v.yaw, v.pitch, v.roll, v.hfov, h / w, rect, samples))
    return polys
