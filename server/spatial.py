"""Metric image alignment and grounded, session-scoped spatial assistance."""

import base64
import math
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import Field

from server.navigation import (
    GuideRequest,
    PropertyMap,
    RouteRequest,
    StrictModel,
    guidance,
    route_plan,
)


class Tracking(StrictModel):
    version: int = Field(default=1, ge=1, le=1)
    sequence: int = Field(ge=0)
    timestamp_ms: float = Field(ge=0)
    pose_age_ms: float = Field(ge=0)
    transform: list[float] = Field(min_length=16, max_length=16)
    tracking: str
    floor: str
    steps: int = Field(ge=0)
    depth_supported: bool
    depth_clear: bool = False
    hazard: bool = False
    jpeg_b64: str | None = Field(default=None, max_length=2_000_000)
    intrinsics: list[float] | None = Field(default=None, min_length=9, max_length=9)


class Command(StrictModel):
    text: str = Field(min_length=1, max_length=300)


class PoseValidation(StrictModel):
    physical_pose_validated: bool = False
    position_error_m: float = Field(ge=0)
    heading_error_deg: float = Field(ge=0)
    drift_m_per_second: float = Field(ge=0)
    drift_m_per_meter: float = Field(ge=0)
    heading_drift_deg_per_second: float = Field(ge=0)


def rigid(values):
    matrix = np.asarray(values, dtype=float).reshape(4, 4)
    rotation = matrix[:3, :3]
    if not (
        np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-4)
        and np.allclose(rotation.T @ rotation, np.eye(3), atol=0.01)
        and abs(np.linalg.det(rotation) - 1) < 0.01
    ):
        raise ValueError("Pose must be a rigid row-major transform")
    return matrix


class LandmarkAligner:
    """NPZ contains SIFT descriptors and corresponding metric API x/y/z landmarks.

    Only reviewed maps with independently measured error bounds can enable guidance.
    Mesh availability alone does not supply correctly registered image features.
    """

    def __init__(self, path):
        self.path = path

    def solve(self, jpeg, intrinsics):
        if not self.path.exists():
            return None
        with np.load(self.path, allow_pickle=False) as archive:
            points = archive["points"].astype(np.float64)
            descriptors = archive["descriptors"].astype(np.float32)
        if (
            points.shape != (len(descriptors), 3)
            or descriptors.shape != (len(points), 128)
            or len(points) < 30
            or not np.isfinite(points).all()
            or not np.isfinite(descriptors).all()
        ):
            raise ValueError("Invalid metric landmark archive")
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
        if frame is None or frame.size > 12_000_000:
            raise ValueError("Invalid alignment image")
        keypoints, live = cv2.SIFT_create(nfeatures=1800).detectAndCompute(frame, None)
        if live is None:
            return None
        matches = cv2.BFMatcher().knnMatch(live, descriptors, k=2)
        good = [
            pair[0]
            for pair in matches
            if len(pair) == 2 and pair[0].distance < 0.7 * pair[1].distance
        ]
        # Enforce unique 3D correspondences.
        good = list({m.trainIdx: m for m in sorted(good, key=lambda m: -m.distance)}.values())
        if len(good) < 30:
            return None
        xyz = points[[m.trainIdx for m in good]]
        uv = np.float64([keypoints[m.queryIdx].pt for m in good])
        K = np.asarray(intrinsics, dtype=float).reshape(3, 3)
        if K[0, 0] <= 0 or K[1, 1] <= 0 or not np.allclose(K[2], [0, 0, 1]):
            raise ValueError("Invalid camera intrinsics")
        ok, rvec, tvec, inliers = cv2.solvePnPRansac(
            xyz, uv, K, None, iterationsCount=200, reprojectionError=3
        )
        if not ok or inliers is None or len(inliers) < 30 or len(inliers) / len(good) < 0.6:
            return None
        ids = inliers[:, 0]
        cells = {(int(x / frame.shape[1] * 3), int(y / frame.shape[0] * 3)) for x, y in uv[ids]}
        if len(cells) < 5:
            return None
        rotation = cv2.Rodrigues(rvec)[0]
        if np.any((xyz[ids] @ rotation.T + tvec.ravel())[:, 2] <= 0):
            return None
        camera_from_map = np.eye(4)
        camera_from_map[:3, :3] = rotation
        camera_from_map[:3, 3] = tvec.ravel()
        # OpenCV right/down/forward -> ARKit right/up/backward camera axes.
        return np.linalg.inv(camera_from_map) @ np.diag([1, -1, -1, 1])


class SpatialSession:
    def __init__(self):
        self.sequence = -1
        self.timestamp = -1
        self.alignment = None
        self.fixed_at = 0
        self.fix_position = None
        self.candidate = None
        self.route = None
        self.segment = 0
        self.pose = None
        self.announced = {}
        self.last_text = "Position is not verified."
        self.last_seen = time.monotonic()
        self.steps = 0
        self.stride = None
        self.calibration_start = None
        self.detail = True
        self.last_narration = -math.inf

    def invalidate(self):
        self.alignment = self.pose = self.candidate = None
        self.calibration_start = None


class SpatialEngine:
    def __init__(self, data_dir: Path, clock=time.monotonic):
        self.data_dir, self.clock = data_dir, clock
        self.sessions = {}
        self.aligner = LandmarkAligner(data_dir / "spatial" / "landmarks.npz")

    def map(self):
        import json

        raw = json.loads((self.data_dir / "spatial" / "map.json").read_text())
        prop = PropertyMap.model_validate(raw["navigation"])
        prop.graph()
        return raw, prop

    def session(self, key):
        session = self.sessions.get(key)
        if session is None or self.clock() - session.last_seen > 120:
            self.sessions.pop(key, None)
            raise ValueError("Session expired; create a new session")
        return session

    def update(self, key, packet: Tracking):
        s = self.session(key)
        now = self.clock()
        if (
            packet.sequence <= s.sequence
            or packet.timestamp_ms <= s.timestamp
            or packet.steps < s.steps
        ):
            raise ValueError("Out-of-order tracking or step reset; create a new session")
        native = rigid(packet.transform)
        gap = now - s.last_seen
        s.sequence, s.timestamp, s.steps, s.last_seen = (
            packet.sequence,
            packet.timestamp_ms,
            packet.steps,
            now,
        )
        if packet.tracking != "normal" or packet.pose_age_ms > 500 or gap > 1:
            s.invalidate()
            return self.stop(s, "Stop. Tracking needs verification.")
        try:
            raw, prop = self.map()
        except (OSError, ValueError, KeyError):
            s.invalidate()
            return self.stop(s, "Metric property map is unavailable.")
        if packet.jpeg_b64 and packet.intrinsics:
            fix = self.aligner.solve(
                base64.b64decode(packet.jpeg_b64, validate=True), packet.intrinsics
            )
            if fix is not None:
                candidate = fix @ np.linalg.inv(native)
                if s.candidate is not None:
                    difference = np.linalg.inv(s.candidate) @ candidate
                    angle = math.degrees(
                        math.acos(np.clip((np.trace(difference[:3, :3]) - 1) / 2, -1, 1))
                    )
                    if np.linalg.norm(difference[:3, 3]) < 0.2 and angle < 8:
                        s.alignment, s.fixed_at = candidate, now
                        s.fix_position = native[:3, 3].copy()
                s.candidate = candidate
        validation = PoseValidation.model_validate(raw.get("validation", {})).model_dump()
        if not validation.get("physical_pose_validated") or s.alignment is None:
            s.pose = None
            return self.stop(s, "Position needs a verified metric alignment.")
        age = now - s.fixed_at
        traveled = float(np.linalg.norm(native[:3, 3] - s.fix_position))
        error = (
            validation["position_error_m"]
            + age * validation["drift_m_per_second"]
            + traveled * validation["drift_m_per_meter"]
        )
        heading_error = (
            validation["heading_error_deg"] + age * validation["heading_drift_deg_per_second"]
        )
        pose = s.alignment @ native
        forward = -pose[:3, 2]
        yaw = math.degrees(math.atan2(forward[0], forward[1])) % 360
        floor = raw["floors"].get(packet.floor)
        if (
            floor is None
            or not floor["camera_z_min"] <= pose[2, 3] <= floor["camera_z_max"]
            or age > 10
            or error > 0.35
            or heading_error > 15
        ):
            s.pose = None
            return self.stop(s, "Stop. Position confidence is too low.")
        s.pose = dict(
            x_m=float(pose[0, 3]),
            y_m=float(pose[1, 3]),
            floor=packet.floor,
            heading_deg=yaw,
            position_uncertainty_m=error,
            heading_uncertainty_deg=heading_error,
        )
        if error <= 0.15:
            position = pose[:3, 3].copy()
            if s.calibration_start is None:
                s.calibration_start = (position, packet.steps, yaw)
            origin, initial_steps, initial_yaw = s.calibration_start
            if abs((yaw - initial_yaw + 180) % 360 - 180) > 10:
                s.calibration_start = (position, packet.steps, yaw)
            elif packet.steps - initial_steps >= 10:
                distance = np.linalg.norm(position - origin)
                stride = distance / (packet.steps - initial_steps)
                if distance >= 5 and 0.2 <= stride <= 1.5:
                    s.stride = float(stride)
                s.calibration_start = (position, packet.steps, yaw)
        if packet.hazard or not packet.depth_supported or not packet.depth_clear:
            return self.stop(s, "Stop. The path is not confirmed clear.")
        if s.route:
            result = guidance(
                prop,
                GuideRequest(
                    map_id=prop.map_id,
                    route=s.route,
                    segment=s.segment,
                    **s.pose,
                    pose_age_ms=packet.pose_age_ms,
                    tracking="normal",
                    stride_m=s.stride or 0.7,
                    stride_calibrated=s.stride is not None,
                ),
            )
            if result["state"] == "WAYPOINT":
                s.segment = result["next_segment"]
            elif result["state"] == "ARRIVED":
                s.route = None
            s.last_text = result["text"]
            return self.event(result["state"], result["text"], 1)
        for feature in self.nearby(raw, s.pose) if s.detail and now - s.last_narration > 6 else []:
            if now - s.announced.get(feature["id"], -math.inf) > 45:
                s.announced[feature["id"]] = now
                s.last_narration = now
                s.last_text = self.describe(feature)
                return self.event("NARRATE", self.describe(feature), 2)
        return {"state": "ORIENTED", "pose": s.pose, "cue": None}

    def event(self, state, text, priority):
        return {
            "state": state,
            "cue": {
                "id": str(uuid.uuid4()),
                "text": text,
                "priority": priority,
                "expires_in_ms": 1000,
                "interrupt": priority == 0,
            },
        }

    def stop(self, session, text):
        session.last_text = text
        return self.event("STOP", text, 0)

    def nearby(self, raw, pose):
        result = []
        for landmark in raw.get("landmarks", []):
            if not landmark.get("verified") or landmark["floor"] != pose["floor"]:
                continue
            dx, dy = landmark["x_m"] - pose["x_m"], landmark["y_m"] - pose["y_m"]
            distance = math.hypot(dx, dy)
            if distance <= 8:
                angle = (math.degrees(math.atan2(dx, dy)) - pose["heading_deg"] + 180) % 360 - 180
                direction = (
                    "ahead"
                    if abs(angle) < 30
                    else "behind you"
                    if abs(angle) > 150
                    else "on your right"
                    if angle > 0
                    else "on your left"
                )
                result.append(landmark | {"distance": distance, "direction": direction})
        return sorted(result, key=lambda f: (not f.get("critical", False), f["distance"]))

    def describe(self, feature):
        return f"The scan shows {feature['label']} {feature['distance']:.1f} meters {feature['direction']}."

    def command(self, key, text):
        s = self.session(key)
        text = text.strip().lower().rstrip("?.!")
        if text in {"stop", "cancel", "stop navigation"}:
            s.route = None
            return self.stop(s, "Navigation cancelled.")
        if text in {"less detail", "more detail"}:
            s.detail = text == "more detail"
            return self.event(
                "ANSWER", "Landmark narration " + ("enabled." if s.detail else "reduced."), 3
            )
        if s.pose is None or self.clock() - s.last_seen > 0.5:
            return self.stop(s, "Current position is not verified.")
        if text == "repeat":
            return self.event("ANSWER", s.last_text, 3)
        raw, prop = self.map()
        if text == "where am i":
            nearest = min(
                (n for n in prop.nodes if n.floor == s.pose["floor"]),
                key=lambda n: math.hypot(n.x_m - s.pose["x_m"], n.y_m - s.pose["y_m"]),
                default=None,
            )
            return self.event(
                "ANSWER",
                f"You are near {nearest.label}."
                if nearest
                else "No verified room label is available.",
                3,
            )
        if text.startswith("take me to "):
            target = text.removeprefix("take me to ")
            destinations = [n for n in prop.nodes if n.label.lower() == target]
            starts = [
                n
                for n in prop.nodes
                if n.floor == s.pose["floor"]
                and math.hypot(n.x_m - s.pose["x_m"], n.y_m - s.pose["y_m"])
                + s.pose["position_uncertainty_m"]
                < 0.4
            ]
            if len(destinations) != 1 or not starts:
                return self.stop(s, "Destination or route starting point needs verification.")
            route = route_plan(
                prop, RouteRequest(start=starts[0].id, destination=destinations[0].id)
            )["route"]
            if len(route) == 1:
                return self.event("ARRIVED", "You are at the destination.", 1)
            s.route, s.segment = route, 0
            return self.event("READY", "Route selected. Checking the path.", 1)
        nearby = self.nearby(raw, s.pose)
        direction = (
            "on your left"
            if "left" in text
            else "on your right"
            if "right" in text
            else "ahead"
            if "ahead" in text
            else None
        )
        if direction:
            nearby = [f for f in nearby if f["direction"] == direction]
        if text.startswith("where is "):
            nearby = [f for f in nearby if text.removeprefix("where is ") in f["label"].lower()]
        answer = (
            " ".join(self.describe(f) for f in nearby[:3])
            or "No verified nearby landmarks match that question."
        )
        s.last_text = answer
        return self.event("ANSWER", answer, 3)


def spatial_router(data_dir):
    import asyncio

    router = APIRouter(prefix="/api/spatial")
    engine = SpatialEngine(data_dir)
    lock = asyncio.Lock()

    @router.get("/readiness")
    async def readiness():
        import json

        try:
            return json.loads((data_dir / "spatial" / "readiness.json").read_text())
        except (OSError, ValueError):
            return {"metric_landmarks_ready": False, "reviewed_routes_ready": False}

    @router.post("/sessions")
    async def create():
        for key in list(engine.sessions):
            if engine.clock() - engine.sessions[key].last_seen > 120:
                del engine.sessions[key]
        if len(engine.sessions) >= 16:
            raise HTTPException(429, "Session limit reached")
        key = str(uuid.uuid4())
        engine.sessions[key] = SpatialSession()
        return {"session_id": key, "version": 1}

    @router.post("/sessions/{key}/tracking")
    async def track(key: str, packet: Tracking):
        try:
            # Serialized at request level by client; offload costly PnP from event loop.
            async with lock:
                return await asyncio.to_thread(engine.update, key, packet)
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, "Invalid tracking, map, or expired session") from exc

    @router.post("/sessions/{key}/command")
    async def command(key: str, request: Command):
        try:
            async with lock:
                return engine.command(key, request.text)
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(422, "Command requires a verified map and active session") from exc

    return router
