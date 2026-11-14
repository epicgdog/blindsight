from server.cues import CueEngine, describe
from server.protocol import Detection, Location


def loc(state="UNCERTAIN", yaw=0):
    return Location("p", "the hallway", state, 0.9, 40, yaw, 0.9, "ref")


def test_specific_counts_bearing_without_invented_depth():
    engine = CueEngine(detailed=True)
    people = [
        Detection("person", 0.9, (0.7, 0.2, 0.85, 0.6)),
        Detection("person", 0.9, (0.75, 0.2, 0.9, 0.6)),
    ]
    for t in [0, 0.2, 0.4]:
        cue = engine.update(people, loc(), t)
    assert cue.text == "2 people ahead to your right, about 1 o'clock."
    info = describe(people[0])
    assert info["bearing_deg"] > 0
    assert info["distance_m"] is None


def test_urgent_warning_short_and_interrupting():
    engine = CueEngine(detailed=True)
    for t in [0, 0.2, 0.4]:
        cue = engine.update([Detection("chair", 0.9, (0.25, 0, 0.75, 1))], loc(), t)
    assert cue.text == "Stop. Chair directly ahead."
    assert cue.interrupt


def test_reviewed_landmarks_only_when_locked_with_heading():
    marks = [
        dict(pano_id="p", label="the exit", yaw_deg=90, reviewed=True),
        dict(pano_id="p", label="invented", yaw_deg=0, reviewed=False),
    ]
    engine = CueEngine(detailed=True, landmarks=marks)
    assert engine.update([], loc(), 0) is None
    assert engine.update([], loc("LOCKED"), 1).text == "View matches the hallway."
    assert engine.update([], loc("LOCKED"), 3).text == "The scan shows the exit at about 3 o'clock."
    assert engine.update([], loc("LOCKED"), 5) is None
    assert engine.update([], loc("LOCKED", yaw=None), 7) is None
    assert "exit" in engine.update([], loc("LOCKED", yaw=90), 9).text


def test_no_main_lobby_fallback_in_detailed_mode():
    place = Location("p", None, "LOCKED", 0.9, 40, 0, 0.9, "r")
    assert "unverified" in CueEngine(detailed=True).update([], place, 0).text
