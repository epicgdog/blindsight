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


def test_api(settings):
    with TestClient(create_app(settings)) as client:
        info = client.get("/api/venue").json()
        assert [lv["id"] for lv in info["levels"]] == ["2", "1", "A"]
        assert client.get(info["levels"][1]["image_url"]).status_code == 200
        res = client.get("/api/venue/search", params={"q": "judges room", "from_room": "117"}).json()
        assert {r["id"] for r in res["results"]} == {"A97", "A98"}
        route = client.get("/api/venue/route", params={"to": "A98", "from_room": "117"}).json()
        assert route["ok"] and route["legs"][-1]["level"] == "A"
        # no start known (no phone yet): routes from check-in
        assert client.get("/api/venue/route", params={"to": "201"}).json()["ok"]
        assert client.get("/api/venue/route", params={"to": "A98", "prefer": "ladder"}).status_code == 422
        assert client.get("/api/venue/search", params={"q": "x", "from_room": "nope"}).status_code == 404
