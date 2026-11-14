"""Evidence from image features that separate plausible competing scan positions."""

import numpy as np

from server.geometry import grid_coverage


def distinctive_support(winner, candidates, points, size, n_min):
    """Count unique geometric inliers not also explained by a credible rival place.

    Input coordinates come from the same extracted live-frame keypoints. Multiple
    reference views of one place are not competing locations. Missing evidence
    fails closed rather than making all points appear distinctive.
    """
    own = points.get(winner.idx)
    rivals = [c for c in candidates if c.place != winner.place and c.inliers >= n_min]
    if not rivals:
        return {"required": False, "count": 0, "grid": 0, "passed": True}
    if own is None or any(points.get(c.idx) is None for c in rivals):
        return {"required": True, "count": 0, "grid": 0, "passed": False}
    rival_points = np.concatenate([points[c.idx] for c in rivals])
    # Exact shared live features are normally identical; tolerate subpixel rounding.
    unique = []
    for point in own:
        if not len(rival_points) or np.min(np.sum((rival_points - point) ** 2, axis=1)) > 4:
            unique.append(point)
    unique = np.unique(np.asarray(unique).reshape(-1, 2), axis=0)
    count = len(unique)
    grid = grid_coverage(unique, *size)
    return {
        "required": True,
        "count": count,
        "grid": grid,
        "passed": count >= max(6, int(np.ceil(winner.inliers * 0.1))) and grid >= 2,
    }
