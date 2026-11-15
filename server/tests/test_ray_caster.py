"""position.ray_caster picks Embree when it is installed and trimesh's pure-Python caster otherwise."""

import sys
from pathlib import Path

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from position import ray_caster  # noqa: E402


def box():
    return trimesh.creation.box(extents=(2.0, 2.0, 2.0))  # centred on the origin


def first_hit_x(rc):
    loc, idx, _ = rc.intersects_location(np.array([[0.0, 0.0, 0.0]]), np.array([[1.0, 0.0, 0.0]]), multiple_hits=False)
    assert len(loc) == 1 and list(idx) == [0]
    return float(loc[0][0])


def test_the_pure_python_caster_is_used_without_embree(monkeypatch):
    from trimesh.ray import ray_triangle

    monkeypatch.setattr(trimesh.ray, "has_embree", False)
    rc = ray_caster(box())
    assert isinstance(rc, ray_triangle.RayMeshIntersector)
    assert first_hit_x(rc) == pytest.approx(1.0)


def test_embree_is_used_when_available():
    pytest.importorskip("embreex")
    from trimesh.ray import ray_pyembree

    if not trimesh.ray.has_embree:
        pytest.skip("trimesh does not see embreex")
    rc = ray_caster(box())
    assert isinstance(rc, ray_pyembree.RayMeshIntersector)
    assert first_hit_x(rc) == pytest.approx(1.0, abs=1e-4)
