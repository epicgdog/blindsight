"""Detailed mode: more things named, several things per sentence, 'getting closer',
what is in view on arrival, and no cue sent while the phone is still talking."""

from server.cues import CueEngine, counted, speech_seconds
from server.protocol import Detection, Location

PEOPLE = [
    Detection("person", 0.9, (0.7, 0.2, 0.85, 0.6)),
    Detection("person", 0.9, (0.75, 0.2, 0.9, 0.6)),
]
BENCH = Detection("bench", 0.9, (0.1, 0.4, 0.25, 0.7))


def loc(state="UNCERTAIN", label="the hallway"):
    return Location("p", label, state, 0.9, 40, 0, 0.9, "ref")


def centred(cls, side):
    return Detection(cls, 0.9, (0.5 - side / 2, 0.3, 0.5 + side / 2, 0.3 + side))


def test_articles_and_plurals():
    assert counted("bench", 1) == "a bench"
    assert counted("oven", 1) == "an oven"
    assert counted("tv", 1) == "a TV"
    assert counted("person", 3) == "3 people"
    assert counted("dining table", 2) == "2 tables"


def test_several_things_in_one_sentence():
    engine = CueEngine(detailed=True)
    for t in (0, 0.2, 0.4):
        cue = engine.update(PEOPLE + [BENCH], loc(), t)
    assert cue.text == "2 people at 1 o'clock and a bench at 11 o'clock."
    assert cue.priority == 3 and not cue.interrupt
    # Both were said, so neither repeats inside the cooldown.
    assert all(engine.update(PEOPLE + [BENCH], loc(), t) is None for t in (2, 3, 4, 5))


def test_things_worth_naming_only_in_detailed_mode():
    laptop = Detection("laptop", 0.9, (0.05, 0.5, 0.2, 0.62))
    legacy, detailed = CueEngine(), CueEngine(detailed=True)
    for t in (0, 0.2, 0.4):
        assert legacy.update([laptop], loc(), t) is None
        cue = detailed.update([laptop], loc(), t)
    assert cue.text == "Laptop ahead to your left, about 11 o'clock."
    assert cue.priority == 4
    # Named once, then left alone for 30 s.
    assert all(detailed.update([laptop], loc(), t) is None for t in (6, 12, 29))
    assert detailed.update([laptop], loc(), 31) is not None


def test_more_hazards_in_detailed_mode():
    dog = Detection("dog", 0.9, (0.4, 0.5, 0.6, 0.9))
    legacy, detailed = CueEngine(), CueEngine(detailed=True)
    for t in (0, 0.2, 0.4):
        assert legacy.update([dog], loc(), t) is None
        cue = detailed.update([dog], loc(), t)
    assert cue.text == "Dog directly ahead."


def test_getting_closer():
    engine = CueEngine(detailed=True)
    cues = []
    t = 0.0
    for _ in range(10):  # standing still: said once, plainly
        cue = engine.update([centred("person", 0.2)], loc(), t)
        cues.append(cue.text if cue else None)
        t += 0.2
    assert [c for c in cues if c] == ["Person directly ahead."]
    for k in range(6):  # the box grows: the gap is shrinking
        cue = engine.update([centred("person", 0.2 + 0.03 * (k + 1))], loc(), t)
        t += 0.2
        if cue:
            break
    assert cue.text == "Person directly ahead, getting closer."
    assert cue.priority == 1 and not cue.interrupt


def test_turning_the_phone_is_not_getting_closer():
    """A box cut off by the image edge grows as it slides into view."""
    engine = CueEngine(detailed=True)
    said = []
    for k in range(12):
        width = 0.1 + 0.03 * k
        cue = engine.update([Detection("chair", 0.9, (0.0, 0.3, width, 0.7))], loc(), k * 0.2)
        said += [cue.text] if cue else []
    assert said and not any("closer" in s for s in said)


def test_arrival_says_what_is_in_view():
    engine = CueEngine(detailed=True)
    assert engine.update(PEOPLE + [BENCH], loc(), 0) is None
    cue = engine.update(PEOPLE + [BENCH], loc("LOCKED"), 0.2)
    assert cue.text == "View matches the hallway. In view: 2 people and a bench."
    # Not repeated one by one straight after.
    assert all(engine.update(PEOPLE + [BENCH], loc("LOCKED"), t) is None for t in (2, 3, 4, 5))


def test_waits_while_the_phone_is_talking_but_warnings_do_not():
    engine = CueEngine(detailed=True)
    for t in (0, 0.2, 0.4):
        first = engine.update(PEOPLE + [BENCH], loc(), t)
    busy_until = 0.4 + speech_seconds(first.text)
    plant = Detection("potted plant", 0.9, (0.3, 0.6, 0.45, 0.9))
    for t in (2.0, 2.2, 2.4, 2.6):
        assert engine.update(PEOPLE + [BENCH, plant], loc(), t) is None
    later = engine.update(PEOPLE + [BENCH, plant], loc(), busy_until + 0.01)
    assert later and "plant" in later.text.lower()
    engine = CueEngine(detailed=True)
    for t in (0, 0.2, 0.4):
        engine.update(PEOPLE + [BENCH], loc(), t)
    wall = Detection("chair", 0.9, (0.2, 0.0, 0.8, 1.0))
    for t in (1.0, 1.2):
        engine.update([wall], loc(), t)
    urgent = engine.update([wall], loc(), 1.4)
    assert urgent.text == "Stop. Chair directly ahead." and urgent.interrupt


def test_location_cue_wording_unchanged_without_objects():
    engine = CueEngine(detailed=True)
    assert engine.update([], loc("LOCKED"), 0).text == "View matches the hallway."


def test_replay_prints_old_and_new_wording(tmp_path):
    import io
    import json

    from server.cue_replay import replay
    from server.cues import describe

    session = tmp_path / "20261114T120000.000000Z-abcd1234"
    session.mkdir()
    rows = []
    for k in range(6):
        location = loc("LOCKED" if k >= 1 else "UNCERTAIN").to_dict()
        detections = [describe(d) for d in PEOPLE + [BENCH]]
        result = {"detections": detections, "location": location}
        rows.append(json.dumps({"offset_s": k * 0.2, "result": result}))
    (session / "frames.jsonl").write_text("\n".join(rows) + "\n")
    out = io.StringIO()
    assert replay(session, out=out) == 0
    text = out.getvalue()
    assert "before  You are in the hallway" in text
    assert "now     View matches the hallway. In view: 2 people and a bench." in text
    assert "6 frames:" in text
