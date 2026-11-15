"""Pitch under 3:00 at 150 wpm, honest numbers, slides, README, hygiene."""
import re
import subprocess

from pptx import Presentation

from .conftest import ROOT


def test_pitch_script_under_three_minutes():
    text = (ROOT / "slides/pitch_script.md").read_text()
    spoken = re.sub(r"^\s*(#|\[|>|---).*$", "", text, flags=re.M)  # drop headings/stage notes
    words = len(re.findall(r"[A-Za-z0-9'’-]+", spoken))
    assert 150 <= words <= 450, f"{words} words = {words/150:.2f} min"


def test_pitch_has_one_sentence_first_and_last():
    text = (ROOT / "slides/pitch_script.md").read_text()
    sentence = "Insight lets blind and low-vision people tour a property independently"
    body = [l for l in text.splitlines() if l.strip() and not l.startswith(("#", "[", ">"))]
    assert sentence in body[0] or sentence in " ".join(body[:2])
    assert sentence in " ".join(body[-2:])


def test_pitch_opens_with_problem_not_team():
    text = (ROOT / "slides/pitch_script.md").read_text().lower()
    first = [l for l in text.splitlines() if l.strip() and not l.startswith("#")][0]
    assert "we are team" not in first and "my name" not in first


def test_pitch_makes_no_unmeasured_claims():
    text = (ROOT / "slides/pitch_script.md").read_text().lower()
    assert "tested with blind users" not in text.replace("not yet tested with blind users", "")


def test_pitch_follows_timed_beats():
    text = (ROOT / "slides/pitch_script.md").read_text()
    for beat in ("0:00", "0:25", "0:50", "2:00", "2:35"):
        assert beat in text, beat


def test_slides_deck():
    prs = Presentation(ROOT / "slides/blindsight.pptx")
    n = len(prs.slides)
    # The team asked for problem, statistics, use-case, future and track slides on top of the
    # original 8; still one message per slide (slides/build_deck.py).
    assert 4 <= n <= 14, "keep slides focused"
    blob = " ".join(sh.text_frame.text for s in prs.slides for sh in s.shapes if sh.has_text_frame).lower()
    for kw in ("vexcel", "graph", "door", "costar"):
        assert kw in blob, kw
    assert "roadmap" in blob


def test_readme_run_commands_and_diagram():
    r = (ROOT / "README.md").read_text()
    assert "```mermaid" in r or "```text" in r
    assert all(k in r.lower() for k in ("phone", "server", "cue"))
    for cmd in ("scripts/tunnel.sh", "echo_server", "uv run pytest"):
        assert cmd in r, cmd


def test_makefile_targets():
    mk = (ROOT / "make/part2.mk").read_text()
    for t in ("part2-echo", "part2-tunnel", "part2-test", "part2-video"):
        assert re.search(rf"^{t}:", mk, re.M), t
    out = subprocess.run(["make", "-n", "-f", "make/part2.mk", "part2-test"], cwd=ROOT,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_secrets_not_tracked():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    assert ".env.local" not in tracked.split()
    ign = subprocess.run(["git", "check-ignore", ".env.local"], cwd=ROOT, capture_output=True)
    assert ign.returncode == 0


def test_expo_app_stack_pinned():
    import json
    pkg = json.loads((ROOT / "app/package.json").read_text())
    deps = pkg["dependencies"]
    for d in ("expo", "expo-camera", "expo-speech", "expo-location", "expo-keep-awake", "expo-screen-orientation"):
        assert d in deps, d
    assert "react-native-vision-camera" not in deps  # Expo Go, no dev build
    app_json = json.loads((ROOT / "app/app.json").read_text())["expo"]
    assert app_json.get("orientation") == "portrait"
