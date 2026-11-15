"""HackPrinceton venue: room search by number and by purpose, routes across levels (server/venue.py)."""

import math

import pytest
from fastapi.testclient import TestClient

from server.server import create_app
from server.venue import Start, Venue, spoken_numbers

CHECK_IN = Start("1", (515, 440))  # JRR atrium, room 117


@pytest.fixture(scope="module")
def venue():
    return Venue.load()


@pytest.mark.parametrize("text, want", [
    ("two oh one", "201"),
    ("one ninety eight", "198"),
    ("a ninety seven", "a 97"),
    ("two hundred one", "201"),
    ("room 198", "room 198"),
])
def test_spoken_numbers(text, want):
    assert spoken_numbers(text) == want


@pytest.mark.parametrize("query, ids", [
    ("judges room", {"A97", "A98"}),
    ("judges", {"A97", "A98"}),
    ("mentors", {"A97", "A98"}),
    ("judging", {"A71"}),
    ("the judging room", {"A71"}),
    ("CoStar", {"A01"}),
    ("sponsor room", {"A01", "A02", "A12"}),
    ("hacker space", {"198", "201", "A17", "A87", "A66"}),
    ("food", {"171"}),
    ("check-in", {"117"}),
    ("where can I get coffee", {"cafe"}),
    ("bathroom", {"restroom-1n", "restroom-An"}),
    ("juges room", {"A97", "A98"}),
])
def test_search_by_purpose(venue, query, ids):
    assert {r["id"] for r in venue.search(query, CHECK_IN)} == ids


@pytest.mark.parametrize("query, rid", [
    ("A97", "A97"), ("a 97", "A97"), ("room A-97", "A97"), ("a ninety seven", "A97"),
    ("198", "198"), ("room 198", "198"), ("one ninety eight", "198"),
    ("201", "201"), ("two oh one", "201"), ("take me to a98", "A98"),
])
def test_search_by_room_number(venue, query, rid):
    assert [r["id"] for r in venue.search(query, CHECK_IN)] == [rid]


def test_unknown_query_finds_nothing(venue):
    assert venue.search("something weird", CHECK_IN) == []


def test_judges_and_judging_are_different_rooms(venue):
    judges = {r["id"] for r in venue.search("judges room")}
    judging = {r["id"] for r in venue.search("judging room")}
    assert judges.isdisjoint(judging)


def test_results_nearest_first_with_level(venue):
    out = venue.search("judges room", CHECK_IN)
    assert [r["distance_m"] for r in out] == sorted(r["distance_m"] for r in out)
    assert all(r["level"] == "A" for r in out)


def test_route_to_judges_room_goes_down_one_level(venue):
    r = venue.route("A98", CHECK_IN)
    assert r["ok"]
    assert [leg["level"] for leg in r["legs"]] == ["1", "A"]
    assert r["transfers"][0]["to_level"] == "A"
    assert r["legs"][-1]["px"][-1] == list(map(float, venue.rooms["A98"]["door"]))
    assert "down to level A" in " ".join(r["steps"])
    assert r["steps"][-1] == "Judges and mentors room A98 is the door on your right."


def test_route_within_a_level_has_one_leg(venue):
    r = venue.route("198", CHECK_IN)
    assert [leg["level"] for leg in r["legs"]] == ["1"]
    assert r["transfers"] == []
    assert r["steps"][-1] == "Hacker space 198 is the door on your right."


def test_route_up_to_level_2(venue):
    r = venue.route("201", CHECK_IN)
    assert [leg["level"] for leg in r["legs"]] == ["1", "2"]
    assert any("up to level 2" in s for s in r["steps"])


def test_route_prefers_and_honours_stairs_or_elevator(venue):
    assert venue.route("A97", CHECK_IN, prefer="elevator")["transfers"][0]["kind"] == "elevator"
    assert venue.route("A71", CHECK_IN, prefer="stairs")["transfers"][0]["kind"] == "stairs"


def test_route_lengths_add_up(venue):
    r = venue.route("A98", CHECK_IN)
    walk = sum(leg["length_m"] for leg in r["legs"])
    assert r["total_m"] >= walk
    for leg in r["legs"]:
        assert len(leg["px"]) == len(leg["m"]) >= 2


def test_approximate_rooms_make_no_left_right_claim(venue):
    r = venue.route("271", CHECK_IN)
    assert "left" not in r["steps"][-1] and "right" not in r["steps"][-1]


def test_every_room_is_reachable_from_check_in(venue):
    for rid in venue.rooms:
        assert venue.route(rid, CHECK_IN)["ok"], rid


def test_pixels_and_metres_round_trip(venue):
    for level in ("1", "2", "A"):
        u, v = venue.to_px(level, 3.2, -7.5)
        x, y = venue.to_map(level, u, v)
        assert math.isclose(x, 3.2, abs_tol=1e-6) and math.isclose(y, -7.5, abs_tol=1e-6)


def test_start_from_tracked_position_picks_the_level_by_height(venue):
    low = venue.start_from_position({"x": 0.0, "y": -5.0, "z": 1.4})
    high = venue.start_from_position({"x": 0.0, "y": -5.0, "z": 5.4})
    assert low.level == "1" and high.level == "2"
    assert venue.start_from_position(None) is None


def test_atrium_void_on_level_2_is_not_walkable(venue):
    # the JRR atrium is open to Level 1 inside the Level 2 ring (no scan floor, X on the plan),
    # and so is the Simpson atrium (X on the plan, no scan)
    assert not venue.walkable("2", (410, 420)) and not venue.walkable("2", (700, 600))
    assert venue.walkable("2", (440, 380))  # ring corridor, north side
    assert venue.walkable("1", (515, 440))  # Level 1 atrium floor under the void


def test_position_in_a_hole_snaps_to_the_nearest_corridor(venue):
    x, y = venue.to_map("2", 410, 420)
    s = venue.start_from_position({"x": x, "y": y, "z": 5.0})
    assert s.level == "2" and venue.walkable("2", s.px)
    assert 0 < s.snapped_m < 3 and s.px[0] < 400  # the west side of the ring, not across the void
    on = venue.start_from_position({"x": 0.0, "y": -5.0, "z": 1.4})
    if venue.walkable("1", venue.to_px("1", 0.0, -5.0)):
        assert on.snapped_m == 0.0


def test_route_never_starts_across_a_hole(venue):
    start = Start("2", venue.snap("2", (410, 420))[0])
    first = venue.route("201", start)["legs"][0]["px"]
    assert venue.clear_line("2", first[0], first[1])


def test_every_traced_corridor_node_on_scanned_levels_is_near_walkable_floor(venue):
    # nodes on stairs or at a corridor end may sit on a wall line; the doors of 161 and 254
    # (unscanned Simpson wing) are approximate on the plan
    known = {("1", "las_e161"), ("2", "d_254")}
    for level in ("1", "2"):
        for name, px in venue.nodes[level].items():
            if (level, name) in known:
                continue
            moved = venue.snap(level, px)[1]
            assert moved < 2.5, (level, name, moved)


def test_no_mask_means_no_snapping():
    import json
    from server.venue import DIR
    v = Venue(json.loads((DIR / "venue.json").read_text()), json.loads((DIR / "align.json").read_text()))
    assert v.snap("2", (410, 420)) == ((410.0, 420.0), 0.0) and v.walkable("2", (410, 420))


def test_api(settings):
    with TestClient(create_app(settings)) as client:
        info = client.get("/api/venue").json()
        assert [lv["id"] for lv in info["levels"]] == ["2", "1", "A"]
        assert client.get(info["levels"][1]["image_url"]).status_code == 200
        res = client.get("/api/venue/search", params={"q": "judges room", "from_room": "117"}).json()
        assert {r["id"] for r in res["results"]} == {"A97", "A98"}
        route = client.get("/api/venue/route", params={"to": "A98", "from_room": "117"}).json()
        assert route["ok"] and route["legs"][-1]["level"] == "A"
        res = client.get("/api/venue/search", params={"q": "judges room"}).json()
        assert res["start_assumed"] is True and [r["id"] for r in res["results"]] == ["A98", "A97"]
        # no start known (no phone yet): routes from check-in
        assert client.get("/api/venue/route", params={"to": "201"}).json()["ok"]
        assert client.get("/api/venue/route", params={"to": "A98", "prefer": "ladder"}).status_code == 422
        assert client.get("/api/venue/search", params={"q": "x", "from_room": "nope"}).status_code == 404


# -- agent integration ---------------------------------------------------------

@pytest.fixture
def state(tmp_path):
    from pathlib import Path

    from server.agent import AgentState
    return AgentState(Path("data") if Path("data/matterport").exists() else tmp_path)


def test_agent_find_place_ranks_from_check_in_without_a_position(state):
    out = state.tool("find_place", {"query": "judges room"})
    assert out["ok"] and out["position_known"] is False
    assert [r["id"] for r in out["results"]] == ["A98", "A97"]


def test_agent_navigate_to_sets_destination_and_returns_steps(state):
    out = state.tool("navigate_to", {"room": "A98"})
    assert out["ok"] and out["to"]["id"] == "A98" and out["start_assumed"] is True
    assert out["steps"][-1].endswith("door on your right.")
    assert state.snapshot()["venue"]["dest"]["id"] == "A98"
    assert "Venue destination: Judges and mentors room A98" in state.context_line()


def test_agent_route_follows_the_tracked_position(state):
    state.tool("navigate_to", {"room": "A98"})
    state.position = {"x": 0.0, "y": -5.0, "z": 1.4, "sigma_m": 0.4}
    view = state.venue_view()
    assert view["you"]["level"] == "1"
    assert view["route"]["start_assumed"] is False


def test_agent_navigate_to_by_purpose_needs_a_single_match(state):
    assert state.set_venue_destination("judging")["to"]["id"] == "A71"
    out = state.set_venue_destination("hacker space")
    assert out["ok"] is False and len(out["candidates"]) > 1


def test_agent_one_destination_at_a_time(state):
    state.tool("navigate_to", {"room": "A98"})
    state.tool("start_navigation", {"destination": "entrance"})
    assert state.venue_dest is None
    state.tool("navigate_to", {"room": "201"})
    assert state.guide.active is False
    state.tool("stop_navigation", {})
    assert state.venue_dest is None


# -- places marked on site -------------------------------------------------------

def test_mark_water_mid_corridor_routes_there_directly(tmp_path):
    v = Venue.load(marked_path=tmp_path / "marked.json")
    place = v.mark("water", Start("1", (450, 360)))
    r = v.route(place["id"], CHECK_IN)
    assert r["ok"] and r["total_m"] < 25  # not via the far corner of the corridor
    assert r["steps"][-1] == "The water fountain is on your right."
    assert [x["id"] for x in v.search("water", CHECK_IN)] == [place["id"]]


def test_marked_places_survive_a_reload_and_can_be_removed(tmp_path):
    path = tmp_path / "marked.json"
    v = Venue.load(marked_path=path)
    rid = v.mark("restroom", Start("A", (345, 520)))["id"]
    v2 = Venue.load(marked_path=path)
    assert rid in v2.rooms and v2.route(rid, CHECK_IN)["ok"]
    assert v2.route("A98", CHECK_IN)["total_m"] == Venue.load(marked_path=None).route("A98", CHECK_IN)["total_m"]
    assert v2.unmark(rid) and not v2.unmark("A97")
    assert rid not in Venue.load(marked_path=path).rooms


def test_mark_rejects_unknown_kinds(tmp_path):
    with pytest.raises(ValueError):
        Venue.load(marked_path=tmp_path / "m.json").mark("pizza", CHECK_IN)


def test_mark_api_needs_a_known_position(settings):
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/venue/mark", json={"kind": "water"}).status_code == 409
