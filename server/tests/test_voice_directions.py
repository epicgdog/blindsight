"""Movement instructions supplied to Gemini, independent of model wording/audio."""

import pytest
from test_agent import make_state, rank

from server.heading import HeadingKalman


def located(tmp_path, yaw=0, position=None):
    state = make_state(tmp_path)
    state.heading = HeadingKalman(state.map.frames)
    for t in (0, 0.5):
        match = {"ranking": rank(("a", 200)), "position": position}
        if yaw is not None:
            match["heading_obs"] = {"pano": "a", "yaw": yaw}
        state.on_frame(t, match, [], {"pano_id": "a", "yaw_deg": yaw})
    return state


def test_start_returns_first_action_and_estimated_steps(tmp_path):
    state = located(tmp_path)
    out = state.tool("start_navigation", {"destination": "entrance"})
    assert out["ok"] and out["navigation_ready"]
    assert out["instruction"] == "Turn right, to your 2 o'clock."
    assert out["direction"]["remaining_steps"] == 10
    assert out["direction"]["steps_estimated"]


def test_start_uses_tracked_position_instead_of_scan_centre(tmp_path):
    state = located(tmp_path, position={"x": 1, "y": 0, "sigma_m": 0.2})
    out = state.tool("start_navigation", {})
    assert state.guide.route.pts[0] == (1, 0)
    assert out["distance_m"] == pytest.approx(5.8)


def test_missing_heading_never_becomes_go_straight(tmp_path):
    state = located(tmp_path, yaw=None)
    state.tool("start_navigation", {})
    out = state.tool("get_direction", {})
    assert not out["ok"] and not out["navigation_ready"]
    assert out["instruction"].startswith("Stop.")
    assert "do_now" not in out


def test_expired_fused_heading_cannot_fall_back_to_old_visual_yaw(tmp_path):
    state = located(tmp_path)
    state.tool("start_navigation", {})
    state._received_at -= 4  # position can still be held, but facing direction is now too uncertain
    out = state.tool("get_direction", {})
    assert state.decision.place == "a"
    assert not out["navigation_ready"]
    assert out["instruction"].startswith("Stop.")


@pytest.mark.parametrize("tool", ["get_direction", "get_route_status", "plan_route", "start_navigation"])
def test_stale_location_cannot_leak_old_movement_instructions(tmp_path, tool):
    state = located(tmp_path)
    state.tool("start_navigation", {})
    state._received_at -= 11  # camera disconnected, but a route was already active
    out = state.tool(tool, {})
    assert out["instruction"].startswith("Stop.")
    assert not out.get("navigation_ready", out.get("direction", {}).get("navigation_ready"))
    context = state.context_line()
    assert "Place: unknown" in context
    assert "Path to the entrance from here: Stop." in context


def test_imu_cannot_keep_route_active_after_visual_location_expires(tmp_path):
    state = located(tmp_path)
    state.tool("start_navigation", {})
    state.on_imu(12, 0)
    assert state.decision.place is None
    assert state.guide.status["paused"]


def test_guidance_events_are_enabled_by_default_and_respect_override(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_EVENTS", raising=False)
    state = located(tmp_path)
    said = []
    state.emit = lambda *event: said.append(event)
    state.tool("start_navigation", {})
    state.guide.last_text = None
    state.on_imu(0.8, 0, moving=False)
    assert said and "Turn right" in said[0][0]
    monkeypatch.setenv("AGENT_EVENTS", "urgent")
    state.guide.last_text = None
    said.clear()
    state.on_imu(1.1, 0, moving=False)
    assert not said


def test_direction_changes_when_user_turns(tmp_path):
    state = located(tmp_path, yaw=90)
    out = state.tool("start_navigation", {})
    assert out["navigation_ready"]
    assert out["instruction"].startswith("Go straight about")


def test_old_arrival_cannot_override_a_new_location(tmp_path):
    state = located(tmp_path)
    state.guide.status = {"active": False, "arrived": True}
    out = state.tool("get_direction", {})
    assert not out.get("at_destination")
    assert out["navigation_ready"]
    assert "Turn right" in out["instruction"]


@pytest.mark.parametrize("destination", ["bathroom", "south staircase", "outdoor kitchen"])
def test_unsupported_destination_is_not_claimed_as_started(tmp_path, destination):
    state = located(tmp_path)
    assert not state.tool("start_navigation", {"destination": destination})["ok"]
