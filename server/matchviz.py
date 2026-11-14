"""Pure helpers behind the viewer's live pano ranking and footprint box (no model code)."""

from __future__ import annotations

import math

import numpy as np

try:
    from server.geometry import cam_to_world
except ImportError:  # imported by localize.py with server/ on sys.path
    from geometry import cam_to_world  # type: ignore[no-redef]


def rank_places(
    sims: np.ndarray,
    places: list[str],
    top: int = 8,
    inliers: dict[str, int] | None = None,
) -> list[dict]:
    """Rank places by their best-matching reference view. Returns [{place, sim, ref, inliers}]."""
    best: dict[str, tuple[float, int]] = {}
    for i, (s, p) in enumerate(zip(sims, places, strict=False)):
        s = float(s)
        if p not in best or s > best[p][0]:
            best[p] = (s, i)
    inliers = inliers or {}
    # Places with verified geometric matches come first (most inliers), then the rest by similarity.
    ranked = sorted(
        best.items(),
        key=lambda kv: (-(inliers.get(kv[0], 0) > 0), -inliers.get(kv[0], 0), -kv[1][0]),
    )[: max(0, top)]
    return [
        {"place": p, "sim": round(s, 4), "ref": int(i), "inliers": int(inliers.get(p, 0))}
        for p, (s, i) in ranked
    ]


def view_rect_polygon(
    yaw: float,
    pitch: float,
    roll: float,
    hfov: float,
    aspect: float,
    rect: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
    samples: int = 6,
) -> list[list[float]]:
    """Outline of a rectangle of a camera image on an equirectangular pano, normalised to 0..1.

    rect is (u1, v1, u2, v2) in 0..1 image coordinates (0,0 = top-left). x = longitude / 360,
    y = 0.5 - latitude / 180. aspect is image height / width. x is unwrapped around the view
    centre (it may be < 0 or > 1); draw copies at +/-1.
    """
    a = math.tan(math.radians(hfov) / 2)
    b = a * aspect
    u1, v1, u2, v2 = rect
    xa, xb = (2 * u1 - 1) * a, (2 * u2 - 1) * a
    ya, yb = (2 * v1 - 1) * b, (2 * v2 - 1) * b
    corners = [(xa, ya), (xb, ya), (xb, yb), (xa, yb)]
    c2w = cam_to_world(yaw, pitch, roll)
    yaw_r = math.radians(yaw)
    points: list[list[float]] = []
    for k in range(4):
        (x0, y0), (x1, y1) = corners[k], corners[(k + 1) % 4]
        for s in range(samples):
            t = s / samples
            ray = c2w @ np.array([x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, 1.0])
            ray = ray / np.linalg.norm(ray)
            lon = math.atan2(ray[0], ray[2])
            lat = math.asin(max(-1.0, min(1.0, float(ray[1]))))
            dlon = (lon - yaw_r + math.pi) % (2 * math.pi) - math.pi
            points.append([round((yaw_r + dlon) / (2 * math.pi), 5), round(0.5 - lat / math.pi, 5)])
    return points


def footprint_polygon(
    yaw: float,
    pitch: float,
    roll: float,
    hfov: float,
    aspect: float,
    samples: int = 6,
) -> list[list[float]]:
    """Outline of the whole camera view on the pano (see view_rect_polygon)."""
    return view_rect_polygon(yaw, pitch, roll, hfov, aspect, (0.0, 0.0, 1.0, 1.0), samples)
