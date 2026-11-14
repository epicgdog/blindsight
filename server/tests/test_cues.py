import pytest

from server.cues import CueEngine, direction, distance
from server.protocol import Detection, Location


def location(state="UNCERTAIN", label=None):
    return Location("pano", label, state, 0.8, 35, 0, 0.9, "ref")


def box(cls="chair", area=0.15, cx=0.5):
    # Full-height boxes make area precise and let tests independently set direction.
    return Detection(cls, 0.9, (cx - area / 2, 0, cx + area / 2, 1))


@pytest.mark.parametrize(
    "angle,expected",
    [
        (0, "ahead"),
        (-27, "left"),
        (27, "right"),
        (-8, "ahead"),
        (8, "ahead"),
        (-8.01, "slightly left"),
        (8.01, "slightly right"),
        (-18, "slightly left"),
        (18, "slightly right"),
        (-18.01, "left"),
        (18.01, "right"),
    ],
)
def test_direction_boundaries(angle, expected):
    assert direction(0.5 + angle / 55) == expected


@pytest.mark.parametrize(
    "area,expected",
    [
        (0.0299, None),
        (0.03, ""),
        (0.0999, ""),
        (0.10, "close"),
        (0.2999, "close"),
        (0.30, "very close"),
    ],
)
def test_distance_boundaries(area, expected):
    assert distance(area, "chair") == expected
    assert distance(0.001, "person") == ""


def test_debounce_and_cooldown():
    engine = CueEngine()
    assert engine.update([box()], location(), 0) is None
    assert engine.update([box()], location(), 0.2) is None
    first = engine.update([box()], location(), 0.4)
    assert first.text == "chair, ahead, close" and first.priority == 1 and not first.interrupt
    assert engine.update([box()], location(), 5.39) is None
    second = engine.update([box()], location(), 5.4)
    assert second and first.id != second.id


def test_consecutive_frames_and_duplicate_objects():
    engine = CueEngine()
    assert engine.update([box(), box(), box()], location(), 0) is None
    assert engine.update([], location(), 1) is None
    assert engine.update([box()], location(), 2) is None
    assert engine.update([box()], location(), 3) is None
    assert engine.update([box()], location(), 4)


def test_p0_priority_and_largest_area():
    engine = CueEngine()
    objects = [box("chair", 0.15), box("person", 0.35), box("couch", 0.4)]
    for t in (0, 0.2):
        assert engine.update(objects, location(), t) is None
    cue = engine.update(objects, location(), 0.4)
    assert cue.text == "couch, ahead, very close" and cue.priority == 0 and cue.interrupt


def test_p0_gap_preempts_normal_gap():
    engine = CueEngine()
    near = box("chair", 0.15)
    for t in (0, 0.1, 0.2):
        cue = engine.update([near], location(), t)
    assert cue
    urgent = box("person", 0.35)
    assert engine.update([urgent], location(), 0.3) is None
    assert engine.update([urgent], location(), 0.4) is None
    assert engine.update([urgent], location(), 0.99) is None
    assert engine.update([urgent], location(), 1.0).priority == 0


def test_state_changes_deferred_but_not_stale():
    engine = CueEngine()
    assert engine.update([], location("LOCKED", "the lobby"), 0).text == "You are in the lobby"
    assert engine.update([], location("LOST"), 0.1) is None
    assert engine.update([], location("LOST"), 1.5).text == "Not sure where you are"
    assert engine.update([], location("LOST"), 9) is None
    engine.update([], location("LOCKED"), 9.1)
    engine.update([], location("LOST"), 9.2)
    assert engine.update([], location("UNCERTAIN"), 20) is None


def test_silence_and_six_word_object_phrases():
    engine = CueEngine()
    assert engine.update([box("car")], location(), 0) is None
    assert engine.update([box("chair", 0.02)], location(), 1) is None
    plant = box("potted plant", 0.31, 0.72)
    for t in (2, 3, 4):
        cue = engine.update([plant], location(), t)
    assert cue and len(cue.text.split()) <= 6


def test_no_cue_storms():
    engine = CueEngine()
    last = -100
    for frame in range(1800):
        now = frame / 10
        cue = engine.update([box("chair", 0.35), box("person", 0.2)], location("LOCKED"), now)
        if cue:
            assert now - last >= 0.8 - 1e-9
            last = now
