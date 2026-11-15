"""Build server/venue/venue.json: rooms, walk graph and level links for the HackPrinceton venue.

Everything is in each level's own image pixels (server/venue/level_<id>.png, the HackPrinceton
maps), read off the plans with a grid overlay. server/venue/align.json turns pixels into map
metres (the Matterport frame used by localization), per level.

Levels: 2 (upper), 1 (ground, entry), A (one below ground). Matterport scans Levels 1 and 2
(the JRR atrium and its ring corridors); Level A and the Simpson side are not scanned.

  uv run python3 tools/venue_graph.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "server" / "venue" / "venue.json"

LEVELS = [
    {"id": "2", "name": "Level 2", "spoken": "level 2", "order": 2, "image": "level_2.png", "scanned": True},
    {"id": "1", "name": "Level 1 (ground floor, entry)", "spoken": "the ground floor", "order": 1,
     "image": "level_1.png", "scanned": True},
    {"id": "A", "name": "Level A (one below ground)", "spoken": "level A, one floor below ground", "order": 0,
     "image": "level_A.png", "scanned": False},
]

# Rooms. door: where the route ends (on the corridor side of the door). label: the room's
# number box on the map (for the highlight). roles come from the map legend.
ROOMS = [
    # Level 1 (ground floor)
    dict(id="117", level="1", building="JRR", label=(488, 436), door=(515, 440), name="JRR atrium (check-in)",
         roles=["atrium", "check-in"], aliases=["check in", "registration", "atrium", "main atrium", "jrr atrium"]),
    dict(id="198", level="1", building="JRR", label=(362, 520), door=(399, 520), name="Hacker space 198",
         roles=["hacker space"], aliases=["hacking space", "hack room", "hacking room", "workspace"]),
    dict(id="101", level="1", building="JRR", label=(364, 353), door=(399, 352), name="Room 101", roles=[], aliases=[]),
    dict(id="102", level="1", building="JRR", label=(364, 288), door=(399, 290), name="Room 102", roles=[], aliases=[]),
    dict(id="144", level="1", building="LAS", label=(734, 478), door=(734, 501), name="Room 144", roles=[], aliases=[]),
    dict(id="171", level="1", building="LAS", label=(770, 606), door=(770, 600), name="Simpson atrium (food)",
         roles=["food", "atrium"], aliases=["food", "meals", "dinner", "lunch", "breakfast", "snacks", "eat",
                                            "simpson atrium", "las atrium"]),
    dict(id="cafe", level="1", building="LAS", label=(836, 576), door=(814, 584), name="Atrium Café",
         roles=["cafe"], aliases=["cafe", "café", "coffee", "atrium cafe"]),
    dict(id="161", level="1", building="LAS", label=(958, 690), door=(935, 690), name="Room 161", roles=[], aliases=[],
         approximate=True),
    dict(id="restroom-1n", level="1", building="JRR", label=(605, 318), door=(602, 350), name="Restroom",
         spoken="the restroom",
         roles=["restroom"], aliases=["bathroom", "toilet", "restroom", "washroom", "mens room", "womens room"],
         note="Seen on the plan as a row of toilets next to the north stairs; not yet checked on site."),
    # Level 2
    dict(id="201", level="2", building="JRR", label=(295, 345), door=(338, 350), name="Hacker space 201",
         roles=["hacker space"], aliases=["hacking space", "hack room", "hacking room", "workspace"]),
    dict(id="200", level="2", building="JRR", label=(311, 449), door=(340, 449), name="Room 200", roles=[], aliases=[]),
    dict(id="217", level="2", building="JRR", label=(471, 440), door=(495, 398), name="Room 217 (hanging room)",
         roles=[], aliases=["hanging room", "floating room", "forum"]),
    dict(id="298", level="2", building="JRR", label=(296, 554), door=(338, 554), name="Room 298", roles=[], aliases=[]),
    dict(id="254", level="2", building="LAS", label=(889, 502), door=(845, 505), name="Room 254", roles=[], aliases=[],
         approximate=True),
    dict(id="271", level="2", building="LAS", label=(712, 693), door=(712, 528), name="Room 271", roles=[], aliases=[],
         approximate=True),
    # Level A (one below ground)
    dict(id="A01", level="A", building="JRR", label=(302, 459), door=(334, 459), name="Sponsor room A01 (CoStar Group)",
         roles=["sponsor"], aliases=["costar", "co star", "costar group", "sponsor room", "sponsors"]),
    dict(id="A02", level="A", building="JRR", label=(301, 382), door=(334, 382), name="Sponsor room A02",
         roles=["sponsor"], aliases=["sponsor room", "sponsors"]),
    dict(id="A12", level="A", building="JRR", label=(420, 410), door=(420, 438), name="Sponsor room A12",
         roles=["sponsor"], aliases=["sponsor room", "sponsors"]),
    dict(id="A17", level="A", building="JRR", label=(424, 516), door=(430, 462), name="Hacker space A17",
         roles=["hacker space"], aliases=["hacking space", "hack room", "hacking room", "workspace", "auditorium"]),
    dict(id="A87", level="A", building="JRR", label=(424, 623), door=(420, 588), name="Hacker space A87",
         roles=["hacker space"], aliases=["hacking space", "hack room", "hacking room", "workspace"]),
    dict(id="A97", level="A", building="JRR", label=(305, 637), door=(334, 637), name="Judges and mentors room A97",
         roles=["judges", "mentors"], aliases=["judges room", "judge room", "mentors room", "mentor room", "mentors",
                                              "mentor", "judges"]),
    dict(id="A98", level="A", building="JRR", label=(305, 567), door=(334, 567), name="Judges and mentors room A98",
         roles=["judges", "mentors"], aliases=["judges room", "judge room", "mentors room", "mentor room", "mentors",
                                              "mentor", "judges"]),
    dict(id="A66", level="A", building="LAS", label=(814, 634), door=(782, 634), name="Hacker space A66",
         roles=["hacker space"], aliases=["hacking space", "hack room", "hacking room", "workspace"]),
    dict(id="A71", level="A", building="LAS", label=(689, 677), door=(690, 650), name="Judging room A71",
         roles=["judging"], aliases=["judging", "judging room", "demo room", "presentations", "present", "demos",
                                     "expo", "lecture hall"]),
    dict(id="restroom-An", level="A", building="JRR", label=(538, 398), door=(535, 428), name="Restroom",
         spoken="the restroom",
         roles=["restroom"], aliases=["bathroom", "toilet", "restroom", "washroom", "mens room", "womens room"],
         note="Seen on the plan as a row of toilets next to the north stairs; not yet checked on site."),
]

# Walk graph per level: named nodes (pixels) and corridors between them. A corridor is a
# chain of nodes walked in a straight line from one to the next.
NODES = {
    "1": {
        # west corridor of the JRR west wing (x 406)
        "w_102": (406, 290), "w_101": (406, 352), "w_n": (406, 365), "w_atr": (406, 440), "w_198": (406, 520),
        "w_s": (406, 505),
        # ring around the atrium: north (y 365), south (y 505), east (x 598)
        "n_atr": (515, 365), "n_stair": (575, 365), "n_wc": (602, 365), "ne": (598, 365), "n_east": (636, 365),
        "s_atr": (515, 505), "se": (598, 505), "s_east": (636, 505),
        "e_atr": (598, 440),
        # atrium floor (117) and its centre
        "atr_w": (420, 440), "atr": (515, 440), "atr_stair": (500, 478),
        # JRR east wing corridor (x 636)
        "ew_n": (636, 220), "ew_s": (636, 680),
        # link to Simpson along y 505
        "l_w171": (708, 505), "l_144": (734, 505), "l_elev": (724, 505), "l_stair": (786, 505), "l_east": (858, 505),
        # Simpson: west strip of the atrium, atrium 171, café, east corridor to 161
        "las_w": (708, 560), "las_atr": (770, 600), "las_cafe": (814, 584), "las_e161": (858, 690),
        # vertical links (door side)
        "stair_n": (575, 348), "elev": (724, 515), "stair_las": (786, 515),
    },
    "2": {
        # ring around the void: west (x 345), north (y 375), east (x 530), south (y 506)
        "w_201": (345, 350), "w_n": (345, 375), "w_200": (345, 449), "w_298": (345, 554), "w_s": (345, 506),
        "w_top": (345, 260), "w_bot": (345, 640),
        "n_stair": (510, 375), "n_217": (495, 375), "ne": (530, 375), "se": (530, 506), "s_mid": (430, 506),
        "atr_stair": (430, 490),
        # JRR east wing corridor (x 574) and the link to Simpson (y 520)
        "ew_n": (574, 200), "ew_mid": (574, 520), "ew_s": (574, 690),
        "l_elev": (662, 520), "l_stair": (718, 520), "l_east": (770, 520),
        "stair_n": (510, 362), "elev": (662, 530), "stair_las": (718, 530),
        "d_217": (495, 398), "d_254": (845, 505), "d_271": (712, 528),
    },
    "A": {
        # west corridor (x 345), north corridor (y 445), south corridor (y 575)
        "w_A02": (345, 382), "w_A01": (345, 459), "w_A98": (345, 567), "w_A97": (345, 637), "w_top": (345, 350),
        "w_n": (345, 445), "w_s": (345, 575), "w_bot": (345, 700),
        "n_A12": (420, 445), "n_A17": (430, 445), "n_stair": (510, 445), "n_wc": (535, 445), "n_east": (588, 445),
        "s_A87": (420, 575), "s_east": (588, 575),
        # JRR east wing corridor (x 588) and the link to Simpson (y 510)
        "ew_n": (588, 320), "ew_link": (588, 510), "ew_s": (588, 740),
        "l_mid": (700, 510), "l_east": (768, 510),
        # Simpson: down the strip east of the core (x 768), band north of the core (y 590), A66, A71
        "las_band_e": (768, 590), "las_band_w": (630, 590), "las_A66": (768, 634), "las_A71": (690, 640),
        "las_A71_w": (630, 640),
        "stair_n": (510, 425), "elev": (655, 592), "stair_las": (715, 592),
    },
}

CORRIDORS = {
    "1": [
        ["w_102", "w_101", "w_n", "w_atr", "w_s", "w_198"],
        ["w_n", "n_atr", "n_stair", "ne", "n_wc", "n_east"],
        ["w_s", "s_atr", "se", "s_east", "l_w171", "l_elev", "l_144", "l_stair", "l_east"],
        ["ne", "e_atr", "se"],
        ["ew_n", "n_east", "s_east", "ew_s"],
        ["w_atr", "atr_w", "atr", "e_atr"], ["n_atr", "atr", "s_atr"], ["atr", "atr_stair"],
        ["l_w171", "las_w", "las_atr", "las_cafe"],
        ["l_east", "las_e161"],
        ["n_stair", "stair_n"], ["l_elev", "elev"], ["l_stair", "stair_las"],
    ],
    "2": [
        ["w_top", "w_201", "w_n", "w_200", "w_s", "w_298", "w_bot"],
        ["w_n", "n_217", "n_stair", "ne"],
        ["w_s", "s_mid", "se"],
        ["ne", "se"],
        ["ew_n", "ew_mid", "ew_s"],
        ["se", "ew_mid", "l_elev", "d_271", "l_stair", "l_east", "d_254"],
        ["s_mid", "atr_stair"],
        ["n_stair", "stair_n"], ["n_217", "d_217"], ["l_elev", "elev"], ["l_stair", "stair_las"],
    ],
    "A": [
        ["w_top", "w_A02", "w_n", "w_A01", "w_A98", "w_s", "w_A97", "w_bot"],
        ["w_n", "n_A12", "n_A17", "n_stair", "n_wc", "n_east"],
        ["w_s", "s_A87", "s_east"],
        ["ew_n", "n_east", "ew_link", "s_east", "ew_s"],
        ["ew_link", "l_mid", "l_east"],
        ["l_east", "las_band_e", "las_A66"],
        ["las_band_e", "stair_las", "elev", "las_band_w"],
        ["las_band_w", "las_A71_w", "las_A71"],
        ["n_stair", "stair_n"],
    ],
}

# Links between levels: same stair or elevator on each level. kind decides the spoken step and
# the cost (metres-equivalent per level crossed).
LINKS = [
    {"id": "jrr-north-stairs", "kind": "stairs", "name": "the north stairs by the restrooms",
     "nodes": {"2": "stair_n", "1": "stair_n", "A": "stair_n"}},
    {"id": "las-elevator", "kind": "elevator", "name": "the Simpson elevator",
     "nodes": {"2": "elev", "1": "elev", "A": "elev"}},
    {"id": "las-stairs", "kind": "stairs", "name": "the Simpson stairs beside the elevator",
     "nodes": {"2": "stair_las", "1": "stair_las", "A": "stair_las"}},
    {"id": "atrium-stairs", "kind": "stairs", "name": "the atrium staircase",
     "nodes": {"2": "atr_stair", "1": "atr_stair"}},
]

# Which corridor node each room's door joins.
DOOR_JOINS = {
    "117": ("1", "atr"), "198": ("1", "w_198"), "101": ("1", "w_101"), "102": ("1", "w_102"),
    "144": ("1", "l_144"), "171": ("1", "las_atr"), "cafe": ("1", "las_cafe"), "161": ("1", "las_e161"),
    "restroom-1n": ("1", "n_wc"),
    "201": ("2", "w_201"), "200": ("2", "w_200"), "217": ("2", "d_217"), "298": ("2", "w_298"),
    "254": ("2", "d_254"), "271": ("2", "d_271"),
    "A01": ("A", "w_A01"), "A02": ("A", "w_A02"), "A12": ("A", "n_A12"), "A17": ("A", "n_A17"),
    "A87": ("A", "s_A87"), "A97": ("A", "w_A97"), "A98": ("A", "w_A98"), "A66": ("A", "las_A66"),
    "A71": ("A", "las_A71"), "restroom-An": ("A", "n_wc"),
}


def main() -> None:
    for r in ROOMS:
        lvl, node = DOOR_JOINS[r["id"]]
        assert lvl == r["level"], r["id"]
        assert node in NODES[lvl], (r["id"], node)
        r["join"] = node
    for lvl, chains in CORRIDORS.items():
        for chain in chains:
            for n in chain:
                assert n in NODES[lvl], (lvl, n)
    for link in LINKS:
        for lvl, n in link["nodes"].items():
            assert n in NODES[lvl], (link["id"], lvl, n)
    data = {"levels": LEVELS, "rooms": ROOMS, "nodes": NODES, "corridors": CORRIDORS, "links": LINKS,
            "source": "HackPrinceton Fall 2026 venue maps (JRR / LAS), traced 2026-11-15"}
    OUT.write_text(json.dumps(data, indent=1))
    print(f"wrote {OUT}: {len(ROOMS)} rooms, {sum(len(v) for v in NODES.values())} nodes, {len(LINKS)} links")


if __name__ == "__main__":
    main()
