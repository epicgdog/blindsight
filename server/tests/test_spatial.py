import json

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.spatial import (
    LandmarkAligner,
    SpatialEngine,
    SpatialSession,
    Tracking,
    rigid,
    spatial_router,
)


def test_metric_pnp_recovers_known_synthetic_camera(tmp_path):
    image = np.random.default_rng(7).integers(0, 256, (480, 640), dtype=np.uint8)
    keypoints, descriptors = cv2.SIFT_create(nfeatures=1800).detectAndCompute(image, None)
    pixels = np.array([k.pt for k in keypoints])
    points = np.column_stack(
        ((pixels[:, 0] - 320) * 4 / 500, (pixels[:, 1] - 240) * 4 / 500, np.full(len(pixels), 4))
    )
    path = tmp_path / "landmarks.npz"
    np.savez(path, points=points, descriptors=descriptors)
    # Lossless image encoding keeps exact descriptor matches for this geometry test.
    encoded = cv2.imencode(".png", image)[1].tobytes()
    pose = LandmarkAligner(path).solve(encoded, [500, 0, 320, 0, 500, 240, 0, 0, 1])
    assert pose is not None
    assert np.allclose(pose, np.diag([1, -1, -1, 1]), atol=0.01)


def packet(**kw):
    return Tracking(
        **(
            dict(
                sequence=1,
                timestamp_ms=1,
                pose_age_ms=0,
                transform=np.eye(4).ravel().tolist(),
                tracking="normal",
                floor="ground",
                steps=0,
                depth_supported=True,
                depth_clear=True,
            )
            | kw
        )
    )


@pytest.fixture
def engine(tmp_path):
    clock = [100.0]
    e = SpatialEngine(tmp_path, clock=lambda: clock[0])
    s = SpatialSession()
    s.last_seen = clock[0]
    e.sessions["test"] = s
    root = tmp_path / "spatial"
    root.mkdir()
    # Test map is synthetic. It must never become a property artifact.
    raw = dict(
        navigation=dict(
            map_id="test",
            nodes=[
                dict(id="a", label="Entry", x_m=0, y_m=0, floor="ground"),
                dict(id="b", label="Exit", x_m=0, y_m=3, floor="ground"),
            ],
            edges=[dict(source="a", target="b", surveyed_clear=True)],
        ),
        floors={"ground": dict(camera_z_min=-1, camera_z_max=2)},
        validation=dict(
            physical_pose_validated=True,
            position_error_m=0.1,
            heading_error_deg=3,
            drift_m_per_second=0.01,
            drift_m_per_meter=0.01,
            heading_drift_deg_per_second=0.1,
        ),
        landmarks=[dict(id="door", label="exit", floor="ground", x_m=0, y_m=3, verified=True)],
    )
    (root / "map.json").write_text(json.dumps(raw))
    s.alignment = np.eye(4)
    # Native forward is -z; rotate it to map north (+y), map up is z.
    s.alignment[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
    s.fixed_at = clock[0]
    s.fix_position = np.zeros(3)
    return e, s, clock


def test_rigid_rejects_scale_reflection_and_nonfinite():
    for value in (np.diag([2, 1, 1, 1]), np.diag([-1, 1, 1, 1]), np.full((4, 4), np.nan)):
        with pytest.raises(ValueError):
            rigid(value.ravel())


def test_relative_narration_dedup_and_grounded_question(engine):
    e, s, _ = engine
    result = e.update("test", packet())
    assert "3.0 meters ahead" in result["cue"]["text"]
    assert e.update("test", packet(sequence=2, timestamp_ms=2))["cue"] is None
    assert "exit" in e.command("test", "What's ahead?")["cue"]["text"]
    assert "No verified" in e.command("test", "What's on my left?")["cue"]["text"]
    assert s.pose["heading_deg"] == 0


@pytest.mark.parametrize(
    "values",
    [
        dict(tracking="limited"),
        dict(pose_age_ms=501),
        dict(hazard=True),
        dict(depth_clear=False),
        dict(depth_supported=False),
        dict(floor="upstairs"),
    ],
)
def test_stop_conditions(engine, values):
    e, _, _ = engine
    assert e.update("test", packet(**values))["state"] == "STOP"


def test_order_gap_expiry_and_route(engine):
    e, s, clock = engine
    e.update("test", packet())
    assert e.command("test", "take me to exit")["state"] == "READY"
    assert e.update("test", packet(sequence=2, timestamp_ms=2))["state"] == "PROCEED"
    with pytest.raises(ValueError):
        e.update("test", packet(sequence=2, timestamp_ms=2))
    clock[0] += 2
    assert e.command("test", "repeat")["state"] == "STOP"
    assert e.update("test", packet(sequence=3, timestamp_ms=3))["state"] == "STOP"
    assert s.alignment is None
    clock[0] += 121
    with pytest.raises(ValueError, match="expired"):
        e.command("test", "repeat")


def test_missing_or_unvalidated_map_never_guides(engine):
    e, s, _ = engine
    path = e.data_dir / "spatial/map.json"
    raw = json.loads(path.read_text())
    raw["validation"]["physical_pose_validated"] = False
    path.write_text(json.dumps(raw))
    assert e.update("test", packet())["state"] == "STOP"
    assert s.pose is None
    path.unlink()
    assert e.update("test", packet(sequence=2, timestamp_ms=2))["state"] == "STOP"


def test_http_contract_and_missing_alignment(tmp_path):
    app = FastAPI()
    app.include_router(spatial_router(tmp_path))
    with TestClient(app) as client:
        key = client.post("/api/spatial/sessions").json()["session_id"]
        result = client.post(f"/api/spatial/sessions/{key}/tracking", json=packet().model_dump())
        assert result.status_code == 200 and result.json()["state"] == "STOP"
        assert (
            client.post(
                f"/api/spatial/sessions/{key}/tracking", json=packet().model_dump()
            ).status_code
            == 422
        )
