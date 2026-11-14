import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.navigation import (
    GuideRequest,
    PropertyMap,
    RouteRequest,
    guidance,
    navigation_router,
    route_plan,
)


@pytest.fixture
def prop():
    return PropertyMap.model_validate(
        {
            "map_id": "survey-v1",
            "nodes": [
                {"id": "a", "label": "Entry", "x_m": 0, "y_m": 0, "floor": "1"},
                {"id": "b", "label": "Hall", "x_m": 0, "y_m": 3, "floor": "1"},
                {"id": "c", "label": "Kitchen", "x_m": 4, "y_m": 3, "floor": "1"},
            ],
            "edges": [
                {"source": "a", "target": "b", "surveyed_clear": True},
                {"source": "b", "target": "c", "surveyed_clear": True},
                {"source": "a", "target": "c"},
            ],
        }
    )


def request(**changes):
    values = dict(
        map_id="survey-v1",
        route=["a", "b", "c"],
        x_m=0,
        y_m=0,
        floor="1",
        heading_deg=0,
        position_uncertainty_m=0.1,
        heading_uncertainty_deg=3,
        pose_age_ms=50,
        tracking="normal",
        stride_m=0.6,
        stride_calibrated=True,
    )
    return GuideRequest(**(values | changes))


def test_route_excludes_unsurveyed_shortcut_and_stairs(prop):
    result = route_plan(prop, RouteRequest(start="a", destination="c"))
    assert result["route"] == ["a", "b", "c"]
    assert result["distance_m"] == 7
    prop.edges[1].stairs = True
    with pytest.raises(ValueError, match="No surveyed"):
        route_plan(prop, RouteRequest(start="a", destination="c"))


def test_steps_recomputed_from_position_and_calibration(prop):
    assert guidance(prop, request())["estimated_steps"] == 5
    assert guidance(prop, request(y_m=1.2))["estimated_steps"] == 3
    assert guidance(prop, request(stride_calibrated=False))["estimated_steps"] is None
    assert guidance(prop, request(heading_deg=90))["turn_deg"] == -90


@pytest.mark.parametrize(
    "change",
    [
        {"hazard": True},
        {"pose_age_ms": 501},
        {"tracking": "limited"},
        {"position_uncertainty_m": 0.36},
        {"heading_uncertainty_deg": 16},
        {"floor": "2"},
        {"x_m": 1},
        {"y_m": 4},
    ],
)
def test_uncertain_or_off_route_stops(prop, change):
    result = guidance(prop, request(**change))
    assert result["state"] == "STOP"
    assert result["estimated_steps"] is None


def test_waypoint_turn_then_arrival(prop):
    assert guidance(prop, request(y_m=3))["next_segment"] == 1
    turn = guidance(prop, request(y_m=3, segment=1))
    assert turn["state"] == "TURN" and turn["turn_deg"] == 90
    assert guidance(prop, request(x_m=4, y_m=3, segment=1))["state"] == "ARRIVED"
    # Don't claim arrival if uncertainty overlaps the arrival boundary.
    assert guidance(prop, request(y_m=2.7, position_uncertainty_m=0.2))["state"] != "WAYPOINT"


def test_reject_invalid_route_map_and_numbers(prop):
    for change in ({"map_id": "old"}, {"route": ["a", "c"]}, {"segment": 2}):
        with pytest.raises(ValueError):
            guidance(prop, request(**change))
    with pytest.raises(ValueError):
        request(x_m=float("nan"))
    prop.nodes.append(prop.nodes[0])
    with pytest.raises(ValueError, match="Duplicate"):
        prop.graph()


def test_http_missing_map_valid_map_and_invalid_route(tmp_path, prop):
    app = FastAPI()
    app.include_router(navigation_router(tmp_path))
    with TestClient(app) as client:
        payload = {"start": "a", "destination": "c"}
        assert client.post("/api/navigation/route", json=payload).status_code == 503
        (tmp_path / "navigation.json").write_text(json.dumps(prop.model_dump()))
        assert client.post("/api/navigation/route", json=payload).json()["distance_m"] == 7
        assert (
            client.post("/api/navigation/guide", json=request().model_dump()).json()[
                "estimated_steps"
            ]
            == 5
        )
        assert (
            client.post("/api/navigation/route", json=payload | {"start": "bad"}).status_code == 422
        )
