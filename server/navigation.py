"""Metric routing contract. Requires a surveyed map and map-aligned phone pose."""

import heapq
import json
import math
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Node(StrictModel):
    id: str = Field(min_length=1)
    label: str
    x_m: float
    y_m: float
    floor: str


class Edge(StrictModel):
    source: str
    target: str
    surveyed_clear: bool = False
    stairs: bool = False


class PropertyMap(StrictModel):
    map_id: str = Field(min_length=1)
    # x east, y north; heading clockwise from north. All positions in meters.
    nodes: list[Node] = Field(min_length=1, max_length=5000)
    edges: list[Edge] = Field(max_length=20000)

    def graph(self):
        nodes = {n.id: n for n in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError("Duplicate node IDs")
        graph = {key: [] for key in nodes}
        for edge in self.edges:
            if edge.source not in nodes or edge.target not in nodes:
                raise ValueError("Unknown edge endpoint")
            a, b = nodes[edge.source], nodes[edge.target]
            distance = math.hypot(a.x_m - b.x_m, a.y_m - b.y_m)
            if distance == 0 or a.floor != b.floor:
                raise ValueError("Edges must have positive length on one floor")
            if edge.surveyed_clear and not edge.stairs:
                graph[a.id].append((b.id, distance))
                graph[b.id].append((a.id, distance))
        return nodes, graph


class RouteRequest(StrictModel):
    start: str
    destination: str


class GuideRequest(StrictModel):
    map_id: str
    route: list[str] = Field(min_length=2, max_length=5000)
    # Client advances segment only after reaching the waypoint; no nearest-edge jumps.
    segment: int = Field(default=0, ge=0)
    x_m: float
    y_m: float
    floor: str
    heading_deg: float = Field(ge=0, lt=360)
    position_uncertainty_m: float = Field(ge=0)
    heading_uncertainty_deg: float = Field(ge=0, le=180)
    pose_age_ms: float = Field(ge=0)
    tracking: str
    stride_m: float = Field(ge=0.2, le=1.5)
    stride_calibrated: bool = False
    hazard: bool = False


def route_plan(prop: PropertyMap, request: RouteRequest):
    nodes, graph = prop.graph()
    if request.start not in nodes or request.destination not in nodes:
        raise ValueError("Unknown start or destination")
    queue = [(0.0, request.start, [request.start])]
    best = {request.start: 0.0}
    while queue:
        distance, current, path = heapq.heappop(queue)
        if current == request.destination:
            return {"map_id": prop.map_id, "route": path, "distance_m": distance}
        if distance > best[current]:
            continue
        for target, length in graph[current]:
            candidate = distance + length
            if candidate < best.get(target, math.inf):
                best[target] = candidate
                heapq.heappush(queue, (candidate, target, path + [target]))
    raise ValueError("No surveyed stair-free route connects these locations")


def guidance(prop: PropertyMap, req: GuideRequest):
    nodes, graph = prop.graph()
    if req.map_id != prop.map_id or req.segment >= len(req.route) - 1:
        raise ValueError("Map version or segment is invalid")
    for source, target in zip(req.route, req.route[1:], strict=False):
        if source not in graph or target not in {n for n, _ in graph[source]}:
            raise ValueError("Route includes an unverified edge")

    def stop(reason):
        return {"state": "STOP", "text": reason, "estimated_steps": None}

    if req.hazard:
        return stop("Stop. Obstacle reported ahead.")
    if (
        req.tracking != "normal"
        or req.pose_age_ms > 500
        or req.position_uncertainty_m > 0.35
        or req.heading_uncertainty_deg > 15
    ):
        return stop("Stop. Position or heading needs verification.")
    a, b = (nodes[key] for key in req.route[req.segment : req.segment + 2])
    if req.floor != a.floor:
        return stop("Stop. Floor does not match the route.")
    dx, dy = b.x_m - a.x_m, b.y_m - a.y_m
    length = math.hypot(dx, dy)
    progress = ((req.x_m - a.x_m) * dx + (req.y_m - a.y_m) * dy) / length**2
    t = max(0, min(1, progress))
    cross_track = math.hypot(req.x_m - a.x_m - t * dx, req.y_m - a.y_m - t * dy)
    if cross_track + req.position_uncertainty_m > 0.75 or progress > 1.1:
        return stop("Stop. You are outside the verified route corridor.")
    distance = math.hypot(b.x_m - req.x_m, b.y_m - req.y_m)
    if distance + req.position_uncertainty_m <= 0.4:
        final = req.segment == len(req.route) - 2
        return {
            "state": "ARRIVED" if final else "WAYPOINT",
            "text": f"Stop. At {b.label}.",
            "next_segment": None if final else req.segment + 1,
            "estimated_steps": 0,
        }
    bearing = math.degrees(math.atan2(b.x_m - req.x_m, b.y_m - req.y_m)) % 360
    turn = (bearing - req.heading_deg + 180) % 360 - 180
    steps = math.ceil(distance / req.stride_m) if req.stride_calibrated else None
    turning = abs(turn) > 15
    text = (
        f"Turn {abs(turn):.0f} degrees {'right' if turn > 0 else 'left'}."
        if turning
        else f"{b.label} is {distance:.1f} meters ahead."
        + (f" Approximately {steps} steps." if steps is not None else "")
    )
    return {
        "state": "TURN" if turning else "PROCEED",
        "text": text,
        "target": b.label,
        "distance_m": distance,
        "turn_deg": turn,
        "estimated_steps": steps,
        "position_uncertainty_m": req.position_uncertainty_m,
        "step_count_is_estimate": True,
    }


def navigation_router(data_dir: Path):
    router = APIRouter(prefix="/api/navigation")

    def load():
        try:
            prop = PropertyMap.model_validate_json((data_dir / "navigation.json").read_text())
            prop.graph()
            return prop
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(503, "Verified metric navigation map unavailable") from exc

    @router.post("/route")
    async def route(request: RouteRequest):
        try:
            return route_plan(load(), request)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/guide")
    async def guide(request: GuideRequest):
        try:
            return guidance(load(), request)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
