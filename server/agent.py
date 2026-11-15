"""Voice agent (demo) on the Gemini Live API: it is the only voice the user hears.

One Live session per phone connection (gemini-3.8-live, v1beta, audio out). Two inputs:
  * the user's hold-to-talk clips (POST /api/agent/talk), sent as 16 kHz PCM between manual
    activity_start / activity_end markers;
  * EVENT lines from the server: the next guidance instruction, a very close obstacle,
    arrival. The agent decides how to say them (it replaced the old cue engine).
Tools answer from the code, never from the model: the place comes from PlaceWindow, route and
turns from EntranceGuide, objects from SceneMemory. Every spoken turn becomes a WAV clip served
at /api/agent/clip/<id>; event clips ride the next frame result to the phone (result["voice"]).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import subprocess
import threading
import time
import uuid
import wave
from collections import OrderedDict, deque
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from server.cues import HAZARDS
from server.entrance_nav import (
    ARRIVE_M,
    EntranceGuide,
    EntranceMap,
    describe_route,
    plan,
    route_sentence,
    start_point,
)
from server.heading import HeadingKalman, wrap180
from server.place_window import PlaceWindow
from server.route import TURN_DEG, clock
from server.scene_memory import SceneMemory
from server.config import HFOV_DEG
from server.obstacles import CLEARANCE_M, ObstacleMap
from server.venue import Start, Venue
from server.voice import pcm_to_wav, wav_response

log = logging.getLogger("insight.agent")

ROOT = Path(__file__).resolve().parents[1]
LIVE_MODEL = "gemini-3.8-live"
VISION_MODEL = "gemini-2.5-flash"
VOICE_NAME = "Kore"
ENTRANCE_PANO = "n0ppizy9u8xcadypd2bawceyc"  # scan 28, also ENTRANCE_ID in server/viewer/logic.js
MAX_AUDIO_BYTES = 5_000_000
VISION_TIMEOUT_MS = 10_000  # one describe_view call must not hang a spoken answer
TALK_TIMEOUT_S = 25.0
HAZARD_REPEAT_S = 6.0
HAZARD_FRAMES = 2  # a very close obstacle must be seen in this many frames in a row
CLIPS_KEPT = 200
AUTO_NAV = os.environ.get("AUTO_NAV", "0") == "1"  # off: the app starts on a blank map; guidance starts from a dropped pin
IMU_LIVE_S = 1.0  # a phone IMU sample this recent: the compass comes from that stream, not from frames
COMPASS_BUF_MS = 3000  # phone compass history kept, to move a frame's visual fix to the present
COMPASS_MATCH_MS = 500  # a buffered compass reading this close to a frame's capture time is used
IMU_GUIDE_S = 0.2  # guidance re-checked from IMU samples at most this often (5 Hz)
COURSE_MIN_MPS = 0.5  # walking faster than this gives a walking direction...
COURSE_MAX_SIGMA_M = 1.5  # ...from a tracked position at least this sure
# Live audio is 24 kHz 16-bit mono (48 000 bytes/s). It reaches the phone as short WAV segments
# pushed over its websocket as soon as they exist: a short first one so speech starts quickly,
# longer ones after so the phone rarely has a gap between segments.
# Each segment is its own fetch + player load on the phone (~150 ms), so 50 ms segments played as
# choppy fragments. 1 s first, 2 s after: one short gap per 2 s of speech.
FIRST_SEGMENT_BYTES = 48_000  # 1 s
SEGMENT_BYTES = 96_000  # 2 s per segment (24 kHz, 16-bit mono)

SYSTEM_PROMPT = """You are Insight: a warm, calm companion walking beside a blind person indoors, talking
with them through their phone. Sound like a friend who knows the building, not a navigation device.

How to talk
- Talk like a person: contractions, varied wording, a little warmth ("Okay, got it", "Nice, you're doing
  great", "Alright, let's head out"). Never sound like you are reading a form.
- Keep it short: usually one or two sentences, because they are walking and listening. No lists.
- Weave actions into the conversation instead of announcing them. Say what you are doing in passing
  ("Sure, let me get you to the entrance. It's about seventeen steps, so first turn a bit to your right.")
  rather than reporting a status.
- Remember the conversation and refer back to it naturally. Answer small talk and side questions, then
  steer back to the walk if one is in progress.
- Never say "event", "context", "tool", "confidence", "scan point" numbers or other system words.

What you are given
- CONTEXT: lines arrive just before the person speaks: what the camera sees now (object, direction,
  distance), what was seen in the last minute, where they are and the current goal and next step. This is
  your own awareness. Use it to answer and to add a useful word about what is around ("there's a bench on
  your right"). Never read it out as a list, and never reply to a CONTEXT line by itself.
- Lines starting with EVENT come from the guidance system while they walk, tagged with their kind:
  - [step] a new instruction. Tell them in your own natural words, one short sentence, maybe with a word of
    encouragement.
  - [reminder] the same instruction again because they have not done it yet. Say it differently from last
    time and shorter ("Still a little more to the right.", "Keep going straight.").
  - [urgent] an obstacle right in front of them. Say it first, short and clear, starting with "Stop", then
    nothing else.
  - [started] you just worked out where they are and the path to the entrance. Tell them warmly that
    you found them, how far it is, and the first instruction, in one or two short sentences.
  - [arrived] they reached the goal. Celebrate briefly.
  Keep every direction, clock position and distance exactly as given (you may say "about six steps"
  for "about 6 steps"), and never invent a direction, distance or object.
- Use the tool's instruction verbatim for movement. Step counts are estimates, never exact counts.
  Never calculate a distance or convert meters yourself. If navigation_ready is false or ok is false,
  say the supplied instruction/reason and do not give a walking direction.

Using the tools (silently, then just talk)
- Where they are: get_location. If unknown, say something like "Hold the phone steady for a second and
  I'll find the path." Do not ask them to walk or look around.
- More about what is around: get_surroundings. A detailed look at the camera image: describe_view.
- Going to the entrance ("get me out", "where's the door", "take me to the entrance"): start_navigation,
  then tell them, in the same sentence, how far it is and the first thing to do if you know it. If the
  position is not known yet, tell them to hold the phone steady and you'll find the path; the first step
  will follow on its own.
- "Which way?" / "where do I go?" / "am I facing the right way?": call get_direction and say the turn and
  distance it gives. If it says the direction is rough, say it as approximate ("roughly to your right").
- For every request for directions, call get_direction or start_navigation now, even if you remember
  a previous answer. Give only its current instruction; do not chain a turn and a walk together.
  plan_route supplies an overview only when explicitly requested. Guidance to
  the entrance runs on its own from the start; if they said stop and later want to go, use
  start_navigation.
- "Stop" / "never mind": stop_navigation. "How far?": get_route_status or the context.
- The destination is the entrance unless they dropped a pin on the map (the CONTEXT goal then says
  "your pin"); guide to whatever the goal is. "Take me to my pin" is start_navigation with 'pin'.
- HackPrinceton rooms (the building is JRR with the Simpson building; levels 2, 1 the ground floor, and A one
  floor below ground): for any room, by number ("A97", "two oh one") or by purpose ("judges room", "judging",
  "sponsors", "CoStar", "hacker space", "food", "check-in", "bathroom"), call find_place with their words.
  "Judges room" (judges and mentors, A97/A98) and "judging room" (A71, where projects are presented) are
  different rooms: say which one you found. Then navigate_to with the room id they want (the nearest one if
  they did not choose), and tell them the level and distance and the first step from its steps.
  "What's next?" during a room route: the CONTEXT venue line has the current next step.
- Only say guidance started or stopped if the tool result says ok."""

TOOLS = [
    ("get_location", "Where the user is: decided place, confidence, tracked position.", {}),
    ("get_surroundings", "Objects in the current camera frame and ones seen in the last minute.", {}),
    ("start_navigation", "Start spoken turn-by-turn guidance to a destination.",
     {"destination": ("STRING", "'entrance', or 'pin' for the spot dropped on the phone's map.")}),
    ("stop_navigation", "Stop the spoken guidance.", {}),
    ("get_route_status", "Progress of the current guidance: next point, meters left.", {}),
    ("get_direction", "Which way to go right now: the turn to make from the way the person faces and how far to "
                      "walk, plus how sure the facing direction is. Call it whenever they ask which way to go.", {}),
    ("plan_route", "The whole path from where the person stands to the entrance: total meters and each "
                   "straight leg with the turn before it (first turn relative to where they face).", {}),
    ("describe_view", "Describe the latest camera image in one sentence.", {}),
    ("find_place", "Find HackPrinceton venue rooms by room number or purpose (judges room, judging, sponsor, "
                   "CoStar, hacker space, food, check-in, cafe, bathroom). Nearest first, with level and meters.",
     {"query": ("STRING", "What they asked for, in their words, e.g. 'judges room' or 'A97'.")}),
    ("navigate_to", "Guide the person to a venue room (an id from find_place). Returns the level, distance and "
                    "the spoken steps, first step relative to where they face.",
     {"room": ("STRING", "Room id from find_place, e.g. 'A98'."),
      "prefer": ("STRING", "Optional: 'stairs' or 'elevator'.")}),
]


def read_env_key(*names: str) -> str | None:
    """API key from the environment, else from .env.local at the repo root (value never logged)."""
    for n in names:
        if os.environ.get(n):
            return os.environ[n].strip()
    try:
        lines = (ROOT / ".env.local").read_text().splitlines()
    except OSError:
        return None
    env = dict(line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#"))
    for n in names:
        if env.get(n, "").strip():
            return env[n].strip().strip('"').strip("'")
    return None


def gemini_client():
    from google import genai

    key = read_env_key("GOOGLE_API_KEY", "GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_API_KEY missing (environment or .env.local)")
    # Live, function calling and audio output all need v1beta for this key.
    return genai.Client(api_key=key, http_options={"api_version": "v1beta"})


def to_pcm16k(audio: bytes) -> bytes:
    """Any phone clip (m4a/AAC, wav) -> raw 16 kHz mono s16le PCM, the Live API input format."""
    if audio.startswith(b"RIFF"):
        try:
            with wave.open(io.BytesIO(audio), "rb") as wav:
                if wav.getnchannels() == 1 and wav.getsampwidth() == 2 and wav.getframerate() == 16000:
                    return wav.readframes(wav.getnframes())
        except (wave.Error, EOFError):
            pass
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "pipe:1"], input=audio, capture_output=True, timeout=20, check=True)
    return out.stdout


def live_config() -> dict:
    decls = []
    for name, desc, params in TOOLS:
        d = {"name": name, "description": desc}
        if params:
            d["parameters"] = {"type": "OBJECT",
                               "properties": {k: {"type": t, "description": s} for k, (t, s) in params.items()}}
        decls.append(d)
    return {
        "response_modalities": ["AUDIO"],
        "system_instruction": SYSTEM_PROMPT,
        "tools": [{"function_declarations": decls}],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": VOICE_NAME}}},
        # Hold-to-talk: the phone says when speech starts and ends, no voice activity detection.
        "realtime_input_config": {"automatic_activity_detection": {"disabled": True}},
    }


class AgentState:
    """Per-frame world state the agent reads: decided place, objects, guidance. Thread safe.

    emit(text, urgent) is called (from the frame worker thread) for every event to speak."""

    def __init__(self, data_dir: Path, entrance: str | None = ENTRANCE_PANO, log_dir: Path | None = None,
                 place_label=None):
        self.data_dir = Path(data_dir)
        self.lock = threading.RLock()
        self.window = PlaceWindow()
        self.memory = SceneMemory()
        self.map = EntranceMap.load(self.data_dir, entrance)
        self.guide = EntranceGuide(self.map)
        try:
            self.venue: Venue | None = Venue.shared()
        except (OSError, ValueError, KeyError):
            log.warning("venue data unavailable", exc_info=True)
            self.venue = None
        self.venue_dest: str | None = None  # HackPrinceton room being guided to
        self.obstacles = ObstacleMap()  # detected objects on the floor; the drawn route bends around them
        self.venue_prefer: str | None = None
        self.heading = HeadingKalman(self.map.frames)
        self.place_label = place_label or self.map.label
        self.decision = self.window.decide(0.0)
        self.position: dict | None = None
        self.yaw_deg: float | None = None
        self.jpeg: bytes | None = None
        self.t = 0.0
        self.log_dir = log_dir
        self.session = None
        self._received_at = time.monotonic()
        self.emit = None
        self.quiet = False  # True while the user holds the talk button
        self._imu_at: float | None = None  # monotonic time of the last phone IMU sample
        self._compass_buf: deque = deque()  # (phone ms, compass deg) from IMU samples
        self._imu_guide_t = -1e9
        self._hazard_seen: dict[tuple, int] = {}
        self._hazard_said: dict[str, float] = {}
        self.reset()

    def reset(self, keep_goal: bool = False) -> None:
        with self.lock:
            self.window.reset()
            self.memory.reset()
            if hasattr(self, "heading"):
                self.heading.reset()
            if not keep_goal:
                self.guide.stop()
                # Guidance to the entrance is on from the start: the route is planned the moment the
                # place is known, from where the person stands and faces. stop_navigation turns it off.
                if AUTO_NAV:
                    self.guide.start(None)
            elif AUTO_NAV and not self.guide.active:
                self.guide.start(None)  # a new phone session always starts guiding to the entrance
            self.decision = self.window.decide(0.0)
            self.position = self.yaw_deg = self.jpeg = None
            self._received_at = time.monotonic()
            self._hazard_seen, self._hazard_said = {}, {}
            self._imu_at, self._compass_buf, self._imu_guide_t = None, deque(), -1e9
            self._last_nav = None
            self.session = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    def write_log(self, row: dict) -> None:
        if self.log_dir is None:
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with (self.log_dir / f"{self.session}.jsonl").open("a") as f:
                f.write(json.dumps(row, default=str) + "\n")
        except OSError:
            log.warning("agent log write failed", exc_info=True)

    def _hazards(self, t: float, described: list[dict]) -> list[str]:
        """Very close obstacles seen HAZARD_FRAMES frames in a row, once per HAZARD_REPEAT_S per class."""
        now = {(d["cls"], d["direction"]) for d in described if d["cls"] in HAZARDS and d.get("distance") == "very close"}
        self._hazard_seen = {k: self._hazard_seen.get(k, 0) + 1 for k in now}
        out = []
        for (cls, direction), n in self._hazard_seen.items():
            if n >= HAZARD_FRAMES and t - self._hazard_said.get(cls, -1e9) >= HAZARD_REPEAT_S:
                self._hazard_said[cls] = t
                name = {"dining table": "table", "potted plant": "plant"}.get(cls, cls)
                out.append(f"Stop. {name} very close, {direction}.")
        return out

    def _compass_at(self, ts_ms: float | None) -> float | None:
        """Buffered phone compass reading closest to a phone-clock time (a frame's capture), or None."""
        if ts_ms is None or not self._compass_buf:
            return None
        ts, c = min(self._compass_buf, key=lambda e: abs(e[0] - ts_ms))
        return c if abs(ts - ts_ms) <= COMPASS_MATCH_MS else None

    def _fuse_frame_heading(self, t: float, match: dict, compass_deg: float | None, moving: bool | None,
                            ts_ms: float | None) -> None:
        """Fold this frame's visual fix and walking direction into the heading (caller holds the lock)."""
        obs = match.get("heading_obs")
        if self._imu_at is not None and t - self._imu_at < IMU_LIVE_S and self._compass_buf:
            # The IMU stream already fused the compass up to now. The frame's visual fix was taken at
            # ts_ms, so move it by the compass change since then: the phone has turned meanwhile.
            now = self._compass_buf[-1][1]
            then = self._compass_at(ts_ms)
            shift = 0.0 if then is None else wrap180(now - then)
            self.heading.update_sensors(t, None, moving)
            self.heading.update_visual(t, obs, now, moving, shift)
        else:
            self.heading.update(t, obs, compass_deg, moving)
        pos = match.get("position") or {}
        vx, vy, sigma = pos.get("vx"), pos.get("vy"), pos.get("sigma_m")
        # Walking direction is the phone's facing mostly; a fast, sure track gives it.
        if (moving is True and vx is not None and vy is not None and sigma is not None
                and math.hypot(vx, vy) > COURSE_MIN_MPS and sigma <= COURSE_MAX_SIGMA_M):
            self.heading.update_course(t, math.degrees(math.atan2(vx, vy)), moving)

    def _guide_events(self, t: float, steps: int | None, fresh: bool) -> list[tuple[str, bool]]:
        """Guidance for this instant (caller holds the lock). Frames and IMU samples share it, so both
        tag starts and repeats the same way."""
        self.decision = self.window.decide(t)
        had_route = self.guide.route is not None
        nav = self.guide.update(t, self.decision.place, self.decision.state, self._guidance_yaw(t), self.position,
                                self.heading.estimate.get("heading_deg"), steps=steps, fresh=fresh)
        if not nav:
            return []
        # The agent says a repeated instruction in new words, so tell it which ones repeat.
        kind = ("arrived" if nav.startswith("You're at") else
                "reminder" if nav == self._last_nav else "step")
        if kind == "step" and not had_route and self.guide.route is not None:
            kind = "started"  # position just found: the first instruction of a fresh route
            nav = f"{nav} (about {self.guide.status.get('remaining_steps')} steps to {self.map.goal_label} in total)"
        self._last_nav = nav
        return [(f"[{kind}] {nav}", False)]

    @staticmethod
    def _deliver(events: list[tuple[str, bool]], emit) -> None:
        # AGENT_EVENTS: "all" (default) speaks guidance on its own; "urgent" speaks only very close
        # obstacle warnings, so the agent otherwise talks only when the mic is used; "none" is silent.
        mode = os.environ.get("AGENT_EVENTS", "all")
        for text, urgent in events:
            if emit is not None and (mode == "all" or (mode == "urgent" and urgent)):
                emit(text, urgent)

    def on_frame(self, t: float, match: dict | None, described: list[dict], location: dict,
                 jpeg: bytes | None = None, compass_deg: float | None = None,
                 moving: bool | None = None, steps: int | None = None,
                 ts_ms: float | None = None, pitch_deg: float | None = None, roll_deg: float | None = None,
                 frame_wh: tuple[int, int] | None = None) -> list[tuple[str, bool]]:
        """Update from one processed frame. Returns (and emits) the events to speak: (text, urgent).
        ts_ms is the frame's capture time on the phone clock (see on_imu)."""
        with self.lock:
            self._received_at = time.monotonic()
            self.t = t
            match = match or {}
            # Every verified frame counts (the server sends match only for matched frames); LOCKED also
            # needs yaw agreement over frames, which the place itself does not depend on.
            ranking = match.get("ranking")
            self.decision = self.window.push(t, ranking)
            self.position = match.get("position")
            place = self.decision.place
            # The localizer's yaw belongs to the place it reports; use it only when that is ours.
            self.yaw_deg = location.get("yaw_deg") if location.get("pano_id") == place else None
            # Fused heading (PnP > matched view > calibrated compass), mesh frame; see server/heading.py.
            self._fuse_frame_heading(t, match, compass_deg, moving, ts_ms)
            self.memory.update(t, described, place)
            self._update_obstacles(t, described, frame_wh, pitch_deg, roll_deg)
            if jpeg is not None:
                self.jpeg = jpeg
            events = [(f"[urgent] {h}", True) for h in self._hazards(t, described)]
            events += self._guide_events(t, steps, fresh=True)
            if self.quiet:
                events = [e for e in events if e[1]]  # only urgent ones while the user is talking
            self.write_log({"t": round(t, 3), "kind": "frame", "decision": self.decision.to_dict(),
                            "top3": (match.get("ranking") or [])[:3], "objects": self.memory.summary(t)["now"],
                            "position": self.position, "yaw_deg": self.yaw_deg, "nav": self.guide.status,
                            "events": events})
            emit = self.emit
        self._deliver(events, emit)
        return events

    def on_imu(self, t: float, compass_deg: float | None, moving: bool | None = None,
               steps: int | None = None, phone_ms: float | None = None) -> list[tuple[str, bool]]:
        """A phone IMU sample (~10 Hz, between frames). Fuses its compass into the heading, and re-checks
        guidance (at most IMU_GUIDE_S apart) so a turn is called without waiting for the next frame.
        phone_ms is the sample's time on the phone clock, kept for correcting visual fixes."""
        with self.lock:
            self._imu_at = t
            if phone_ms is not None and compass_deg is not None:
                if self._compass_buf and phone_ms < self._compass_buf[-1][0]:
                    self._compass_buf.clear()  # the phone's clock went back: old readings are not comparable
                self._compass_buf.append((float(phone_ms), float(compass_deg) % 360.0))
                while self._compass_buf[-1][0] - self._compass_buf[0][0] > COMPASS_BUF_MS:
                    self._compass_buf.popleft()
            self.heading.update_sensors(t, compass_deg, moving)
            events = []
            if self.guide.active and t - self._imu_guide_t >= IMU_GUIDE_S:
                self._imu_guide_t = t
                events = self._guide_events(t, steps, fresh=False)
            if self.quiet:
                events = [e for e in events if e[1]]
            emit = self.emit
        self._deliver(events, emit)
        return events

    def _guidance_yaw(self, t: float) -> float | None:
        # A rejected/expired fused heading must not silently fall back to an old frame's yaw.
        return self.yaw_deg if self.heading.x is None and t - self.t <= IMU_LIVE_S else None

    def direction(self) -> dict:
        """Which way to go right now: the next turn and its distance along the route (the guide's
        route while guiding, a fresh one otherwise), plus heading info."""
        with self.lock:
            now = self.t + time.monotonic() - self._received_at
            self.decision = self.window.decide(now)
            self.heading.update_sensors(now, None)
            d, est = self.decision, dict(self.heading.estimate)
            if d.place is None or d.state == "unknown":
                self.guide.status = {"active": self.guide.active, "paused": True}
                return {"ok": False, "navigation_ready": False,
                        "instruction": "Stop. Hold the phone steady while I find your location.",
                        "reason": "current location unavailable"}
            instruction = None
            if self.guide.active:
                instruction = self.guide.update(now, d.place, d.state, self._guidance_yaw(now), self.position,
                                                est.get("heading_deg"), fresh=False)
            xy, _ = start_point(self.map, d.place, self.position) if d.place in self.map.points else (None, None)
            goal = self.map.goal_xy  # the destination point: the entrance, or a dropped pin
            if self.guide.status.get("arrived") and xy is not None and goal is not None and math.dist(xy, goal) <= ARRIVE_M:
                return {"ok": True, "at_destination": True, "navigation_ready": False,
                        "instruction": f"You're at {self.map.goal_label}."}
            if self.guide.status.get("arrived"):
                self.guide.status = {"active": self.guide.active}
            guided = self.guide.active and self.guide.route is not None
            nav = dict(self.guide.status)
            fresh = None if guided else describe_route(self.map, d.place, self._guidance_yaw(now), self.position,
                                                       est.get("heading_deg"), stride=self.guide.stride)
        if guided:
            if "next_m" not in nav:
                return {"ok": False, "navigation_ready": False,
                        "instruction": instruction or "Stop. No route is available from here."}
            turn = nav.get("turn_deg")
            if turn is None:
                return {"ok": False, "navigation_ready": False,
                        "instruction": "Stop. Hold the phone facing forward while I determine your heading.",
                        "reason": "facing direction unavailable"}
            do_now = ("go straight" if turn is not None and abs(turn) <= TURN_DEG else
                      f"turn {'right' if turn > 0 else 'left'} to your {clock(turn)} o'clock")
            out = {"ok": True, "do_now": do_now, "then_walk_m": nav["next_m"], "next_turn": nav["next"],
                   "next_turn_in_m": nav["next_m"], "remaining_m": nav["remaining_m"],
                   "then_walk_steps": nav.get("next_steps"),
                   "next_turn_in_steps": nav.get("next_steps"), "remaining_steps": nav.get("remaining_steps")}
        else:
            if not fresh.get("ok"):
                return {**fresh, "navigation_ready": False,
                        "instruction": "Stop. " + fresh["reason"] + "."}
            if fresh.get("at_destination"):
                return {"ok": True, "at_destination": True, "navigation_ready": False,
                        "instruction": "You're at the entrance."}
            if not fresh.get("heading_known"):
                return {"ok": False, "navigation_ready": False,
                        "instruction": "Stop. Hold the phone facing forward while I determine your heading.",
                        "reason": "facing direction unavailable"}
            leg = fresh["legs"][0]
            out = {"ok": True, "do_now": leg["do"], "then_walk_m": leg["meters"], "then_walk_steps": leg.get("steps"), "next_turn": fresh["next_turn"],
                   "next_turn_in_m": fresh["next_turn_in_m"], "remaining_m": fresh["total_m"]}
        out.update(navigation_ready=True, steps_estimated=True,
                   instruction=instruction or (f"{out['do_now'].capitalize()}." if out['do_now'] != "go straight"
                                                else f"Go straight about {out['then_walk_steps']} steps."),
                   heading_source=est.get("source"), heading_accuracy_deg=est.get("sigma_deg"))
        if est.get("heading_deg") is None:
            out["heading_source"] = "recent_visual_yaw"
            out["note"] = "facing direction is from a recent camera frame; say the direction as approximate"
        elif (est.get("sigma_deg") or 0) >= 20:
            out["note"] = "facing direction is approximate: say 'roughly' but retain the instruction's clock position"
        return out

    def context_line(self) -> str:
        """What the agent should know when the person speaks: view, recent objects, place, goal."""
        direction = self.direction()
        with self.lock:
            d, mem, nav = self.decision, self.tool("get_surroundings", {}), dict(self.guide.status)
        now = "; ".join(mem["now"]) or "nothing detected"
        recent = "; ".join(f"{r['object']} {r['seconds_ago']} s ago ({r['last_direction']})" for r in mem["recent"][:8])
        place = (f"{self.place_label(d.place)} (confidence {d.conf:.2f}"
                 f"{', at the entrance' if d.place == self.map.goal else ''})") if d.place else "unknown"
        if nav.get("active"):
            goal = f"guiding to {self.map.goal_label}"
            if nav.get("paused") or not nav.get("next"):
                goal += ", waiting for a position fix (phone held steady)"
            else:
                goal += (f", next {nav['next']} in about {nav.get('next_steps')} steps, {nav.get('remaining_steps')} steps left in total"
                         + (f", turn {nav['turn_deg']} deg" if nav.get("turn_deg") is not None else ""))
        elif nav.get("arrived"):
            goal = f"arrived at {self.map.goal_label}"
        else:
            goal = "none (not navigating)"
        est = self.heading.estimate
        facing = ("unknown" if est.get("heading_deg") is None else
                  f"known from {est['source']}, about ±{est['sigma_deg']:g} deg")
        with self.lock:
            route = route_sentence(describe_route(self.map, d.place, self._guidance_yaw(self.t + time.monotonic() - self._received_at), self.position, self.heading.estimate.get("heading_deg"), stride=self.guide.stride))
        if not direction.get("navigation_ready"):
            route = direction["instruction"]
        venue = ""
        with self.lock:
            vr = self.venue_route()
        if vr and vr.get("ok"):
            venue = (f" Venue destination: {vr['to']['name']} on {vr['to']['level_name']}, about {vr['total_m']:.0f} m"
                     f"{' (counted from check-in: position unknown)' if vr.get('start_assumed') else ''};"
                     f" next step: {vr['steps'][0]}")
        return f"CONTEXT: Current instruction: {direction['instruction']} Path to {self.map.goal_label} from here: {route}. In view now: {now}. Seen in the last minute: {recent or 'nothing'}. Place: {place}. Facing direction: {facing}. Goal: {goal}.{venue}"

    def snapshot(self) -> dict:
        with self.lock:
            self.decision = self.window.decide(self.t + time.monotonic() - self._received_at)
            est = self.heading.estimate
            heading = {"heading_deg": est.get("heading_deg"), "sigma_deg": est.get("sigma_deg"),
                       "source": est.get("source"),
                       # raw phone compass (magnetic/true north, not the map frame), for comparison
                       "compass_deg": self._compass_buf[-1][1] if self._compass_buf else None}
            return {"decision": self.decision.to_dict(), "place_label": self.place_label(self.decision.place),
                    "nav": self.guide.status, "heading": heading, "path": self._path_view(),
                    "venue": self.venue_view()}

    # -- obstacles on the floor ------------------------------------------------
    def _update_obstacles(self, t, described, frame_wh, pitch_deg, roll_deg) -> None:
        try:
            self.obstacles.update(t, described, frame_wh, pitch_deg, roll_deg, self.position,
                                  self.heading.estimate.get("heading_deg"), HFOV_DEG)
            if self.venue is None:
                return
            by_level: dict[str, list] = {}
            for o in self.obstacles.active():
                level = self.venue.level_of(o.z)
                if level is None:
                    continue
                u, v = self.venue.to_px(level, o.x, o.y)
                by_level.setdefault(level, []).append((u, v, (o.radius_m + CLEARANCE_M) * self.venue.scale(level)))
            self.venue.set_obstacles(by_level)
        except Exception:  # obstacles must never break guidance
            log.exception("obstacle update failed")

    def obstacle_view(self) -> list[dict]:
        out = []
        for o in self.obstacles.active():
            d = o.to_dict()
            if self.venue is not None:
                level = self.venue.level_of(o.z)
                if level is not None:
                    d["level"] = level
                    d["px"] = [round(v, 1) for v in self.venue.to_px(level, o.x, o.y)]
                    d["r_px"] = round(o.radius_m * self.venue.scale(level), 1)
            out.append(d)
        return out

    # -- HackPrinceton venue rooms ----------------------------------------------
    def venue_start(self) -> Start | None:
        """Where the person is on the venue plans: the tracked position (level from its height),
        else the decided scan point (Level 1 scan points), else unknown."""
        if self.venue is None:
            return None
        start = self.venue.start_from_position(self.position)
        if start is None and self.decision.place in self.map.points:
            x, y = self.map.points[self.decision.place]
            start = Start("1", self.venue.to_px("1", x, y))
        return start

    def venue_route(self) -> dict | None:
        """The route to the venue destination from where the person is now (from check-in, 117,
        while the position is unknown), first step relative to the way they face."""
        if self.venue is None or self.venue_dest is None:
            return None
        start, assumed = self.venue_start(), False
        if start is None:
            check_in = self.venue.rooms["117"]
            start, assumed = Start(check_in["level"], tuple(check_in["door"])), True
        heading = None if assumed else self.heading.estimate.get("heading_deg")
        out = self.venue.route(self.venue_dest, start, self.venue_prefer, heading)
        out["start_assumed"] = assumed
        return out

    def venue_view(self) -> dict | None:
        """For the phone: where the person is on the plans and the live route, if any."""
        if self.venue is None:
            return None
        start = self.venue_start()
        return {"you": None if start is None else {"level": start.level, "px": [round(v, 1) for v in start.px],
                                                   "snapped_m": start.snapped_m},
                "dest": None if self.venue_dest is None else self.venue.room_info(self.venue_dest),
                "obstacles": self.obstacle_view(),
                "route": self.venue_route()}

    def set_venue_destination(self, room: str | None, prefer: str | None = None) -> dict:
        """Guide to a venue room (None clears it). The entrance/pin guidance stops meanwhile:
        one destination at a time."""
        with self.lock:
            if room is None:
                self.venue_dest, self.venue_prefer = None, None
                return {"ok": True, "cleared": True}
            if self.venue is None:
                return {"ok": False, "reason": "venue map unavailable"}
            rid = next((k for k in self.venue.rooms if k.lower() == str(room).strip().lower()), None)
            if rid is None:
                hits = self.venue.search(str(room), self.venue_start())
                if len(hits) != 1:
                    return {"ok": False, "reason": f"no single room matches '{room}'",
                            "candidates": [h["id"] for h in hits]}
                rid = hits[0]["id"]
            if prefer not in (None, "", "stairs", "elevator"):
                return {"ok": False, "reason": "prefer must be stairs or elevator"}
            self.venue_dest, self.venue_prefer = rid, prefer or None
            self.guide.stop()
            route = self.venue_route()
            return {"ok": bool(route and route.get("ok")), "to": self.venue.room_info(rid),
                    "total_m": route.get("total_m") if route else None,
                    "steps": route.get("steps") if route else [],
                    "start_assumed": route.get("start_assumed") if route else None}

    def _path_view(self) -> dict:
        """The route for the viewer (map frame, metres): the one being guided, else a preview planned
        from the decided place and tracked position. Plus the entrance, for a straight-line comparison."""
        route, guiding = self.guide.route, self.guide.route is not None
        if route is None and self.decision.place in self.map.points:
            route = plan(self.map, *start_point(self.map, self.decision.place, self.position))
        goal, dest = self.map.goal_xy, self.map.destination()
        ent = self.map.points.get(self.map.entrance)
        return {"pts": [[round(x, 2), round(y, 2)] for x, y in route.pts] if route else None,
                "guiding": guiding, "length_m": round(route.length, 1) if route else None,
                "goal": [round(goal[0], 2), round(goal[1], 2)] if goal else None,
                "goal_label": dest["label"], "goal_kind": dest["kind"],
                "entrance": [round(ent[0], 2), round(ent[1], 2)] if ent else None}

    # -- destination (dropped pin) ---------------------------------------------
    def preview_destination(self, x: float, y: float) -> dict:
        """Where a pin at (x, y) would land and the route to it, without changing anything."""
        import copy
        with self.lock:
            m = copy.copy(self.map)  # shares points, walls and graph; only the goal fields differ
            out = m.set_destination((x, y))
            if not out.get("ok"):
                return out
            d = self.window.decide(self.t + time.monotonic() - self._received_at)
            route = plan(m, *start_point(m, d.place, self.position)) if d.place in m.points else None
        out.update(length_m=round(route.length, 1) if route else None,
                   pts=[[round(a, 2), round(b, 2)] for a, b in route.pts] if route else None)
        return out

    def set_destination(self, x: float | None = None, y: float | None = None, label: str = "your pin") -> dict:
        """Guide to a pin at (x, y), or back to the entrance when x is None. Starts guidance now."""
        with self.lock:
            out = self.map.clear_destination() if x is None else self.map.set_destination((x, y), label)
            if not out.get("ok"):
                return out
            self.decision = self.window.decide(self.t + time.monotonic() - self._received_at)
            started = self.guide.replan(self.decision.place, self.position)
            self._last_nav = None
            route = self.guide.route
            return {**out, "active": self.guide.active, "length_m": round(route.length, 1) if route else None}

    # -- tools -----------------------------------------------------------------
    def tool(self, name: str, args: dict, describe=None) -> dict:
        with self.lock:
            self.decision = self.window.decide(self.t + time.monotonic() - self._received_at)
            d = self.decision
            if name == "get_location":
                if d.place is None:
                    return {"place": "unknown", "confidence": 0.0,
                            "note": "no confident match in the last 10 seconds"}
                out = {"place": self.place_label(d.place), "is_entrance": d.place == self.map.goal,
                       "confidence": round(d.conf, 2), "state": d.state}
                if self.position:
                    out["position_sigma_m"] = self.position.get("sigma_m")
                return out
            if name == "get_surroundings":
                now = self.t + time.monotonic() - self._received_at
                result = self.memory.summary(now, self.place_label)
                if now - self.t > 1:
                    result["now"] = []
                    result["note"] = "camera observations are stale"
                result["recent"] = [r for r in result["recent"] if r["seconds_ago"] <= 60]
                return result
            if name == "start_navigation":
                dest = str(args.get("destination") or "entrance").strip().lower()
                if "pin" in dest or "marked" in dest:
                    if self.map.goal_kind != "pin":
                        return {"ok": False, "reason": "no pin is dropped; they can drop one on the phone's map"}
                else:
                    if dest not in {"entrance", "the entrance", "main entrance", "front entrance",
                                    "door", "the door", "front door", "exit", "the exit", "out", "outside"}:
                        return {"ok": False, "reason": "only the entrance or the dropped pin are supported"}
                    if self.map.goal_kind != "entrance":
                        self.map.clear_destination()
                self.venue_dest = None  # one destination at a time
                result = self.guide.start(d.place, self.position if d.place else None)
                if not result.get("ok"):
                    return result
                direction = self.direction()
                return {**result, "direction": direction,
                        "navigation_ready": direction.get("navigation_ready", False),
                        "instruction": direction["instruction"]}
            if name == "stop_navigation":
                self.venue_dest = None
                return self.guide.stop()
            if name == "find_place":
                if self.venue is None:
                    return {"ok": False, "reason": "venue map unavailable"}
                start = self.venue_start()
                check_in = self.venue.rooms["117"]
                # unknown position: rank by distance from check-in, the usual starting point
                hits = self.venue.search(str(args.get("query") or ""),
                                         start or Start(check_in["level"], tuple(check_in["door"])))
                return {"ok": bool(hits), "position_known": start is not None,
                        "results": [{k: h.get(k) for k in ("id", "name", "level_name", "roles", "distance_m", "note")}
                                    for h in hits] or None,
                        "note": None if hits else "no room matches; ask them to say it another way"}
            if name == "navigate_to":
                return self.set_venue_destination(args.get("room"), args.get("prefer"))
            if name == "get_route_status":
                direction = self.direction()
                status = dict(self.guide.status) if direction.get("navigation_ready") else {
                    "active": self.guide.active, "paused": not direction.get("at_destination", False),
                    "arrived": direction.get("at_destination", False)}
                return {**status, "direction": direction, "instruction": direction["instruction"]}
            if name == "get_direction":
                return self.direction()
            if name == "plan_route":
                direction = self.direction()
                return {**describe_route(self.map, d.place, self._guidance_yaw(self.t + time.monotonic() - self._received_at), self.position,
                                         self.heading.estimate.get("heading_deg"), stride=self.guide.stride),
                        "navigation_ready": direction.get("navigation_ready", False),
                        "instruction": direction["instruction"]}
            jpeg = self.jpeg
        if name == "describe_view":
            if jpeg is None or describe is None:
                return {"ok": False, "reason": "no camera image yet"}
            return {"description": describe(jpeg)}
        return {"ok": False, "reason": f"unknown tool {name}"}


class LiveAgent:
    """One Gemini Live session (async, on the server's event loop) plus the clips it spoke."""

    def __init__(self, state: AgentState, client=None, model: str | None = None):
        self.state = state
        self.model = model or os.environ.get("GEMINI_LIVE_MODEL", LIVE_MODEL)
        self._client = client
        self._cm = None
        self.session = None
        self._reader: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self.pending: deque[dict] = deque()  # inputs waiting for their spoken turn, oldest first
        self._turn = self._new_turn()
        self.clips: OrderedDict[str, bytes] = OrderedDict()
        self.outbox: deque[dict] = deque()  # voice segments not yet sent (no phone attached)
        self.sink = None  # sink(msg): push one voice message to the phone now (set by the websocket)
        state.emit = self._emit_threadsafe

    @staticmethod
    def _new_turn() -> dict:
        return {"heard": "", "said": "", "audio": bytearray(), "tools": [], "buf": bytearray(),
                "id": uuid.uuid4().hex[:8], "segments": 0}

    def client(self):
        if self._client is None:
            self._client = gemini_client()
        return self._client

    # -- session ---------------------------------------------------------------
    async def ensure(self):
        async with self._connect_lock:
            if self.session is not None:
                return self.session
            self._loop = asyncio.get_running_loop()
            self._cm = self.client().aio.live.connect(model=self.model, config=live_config())
            self.session = await self._cm.__aenter__()
            self._turn = self._new_turn()
            self._reader = asyncio.create_task(self._read())
            log.info("Live session open (%s)", self.model)
            return self.session

    async def close(self) -> None:
        task, cm = self._reader, self._cm
        self._reader = self._cm = self.session = None
        if task is not None:
            task.cancel()
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        while self.pending:
            p = self.pending.popleft()
            if p.get("future") is not None and not p["future"].done():
                p["future"].set_exception(RuntimeError("Live session closed"))

    async def reset(self) -> None:
        await self.close()
        self.outbox.clear()

    async def _read(self) -> None:
        s = self.session
        try:
            while True:
                async for r in s.receive():
                    if r.tool_call:
                        await self._answer_tools(s, r.tool_call.function_calls)
                    sc = r.server_content
                    if sc is None:
                        continue
                    t = self._turn
                    if sc.input_transcription and sc.input_transcription.text:
                        t["heard"] += sc.input_transcription.text
                    if sc.output_transcription and sc.output_transcription.text:
                        t["said"] += sc.output_transcription.text
                    for part in (sc.model_turn.parts if sc.model_turn else []) or []:
                        if part.inline_data and part.inline_data.data:
                            t["audio"] += part.inline_data.data
                            t["buf"] += part.inline_data.data
                            limit = FIRST_SEGMENT_BYTES if t["segments"] == 0 else SEGMENT_BYTES
                            if len(t["buf"]) >= limit:
                                self._push_segment(t, last=False)
                    # Every completed or interrupted turn is finished, with or without audio: a silent
                    # turn left unfinished shifted every later answer onto the wrong request, and a
                    # talk then waited for a turn that never came (the phone timed out).
                    # Except a turn that ended on a tool call: Live completes it silently, then speaks
                    # the result in a second turn. Keep collecting until that one.
                    if sc.turn_complete and t.pop("await_reply", False) and not t["audio"]:
                        continue
                    if sc.turn_complete or getattr(sc, "interrupted", False):
                        if t["audio"] or t["segments"]:
                            self._push_segment(t, last=True)
                        self._finish_turn()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a dropped session reconnects on next use
            log.exception("Live session ended")
        finally:
            if self.session is s:
                self.session = None
                for p in list(self.pending):
                    if p.get("future") is not None and not p["future"].done():
                        p["future"].set_exception(RuntimeError("Live session ended"))
                self.pending.clear()

    async def _answer_tools(self, s, calls) -> None:
        from google.genai import types

        responses = []
        for fc in calls or []:
            args = dict(fc.args or {})
            result = await asyncio.to_thread(self.state.tool, fc.name, args, self.describe)
            self._turn["tools"].append({"name": fc.name, "args": args, "result": result})
            self._turn["await_reply"] = True
            responses.append(types.FunctionResponse(id=fc.id, name=fc.name, response={"result": result}))
        async with self._send_lock:
            await s.send_tool_response(function_responses=responses)

    def _store(self, pcm: bytes) -> str:
        clip_id = uuid.uuid4().hex[:12]
        self.clips[clip_id] = pcm_to_wav(pcm, 24000)
        while len(self.clips) > CLIPS_KEPT:
            self.clips.popitem(last=False)
        return clip_id

    def _push_segment(self, t: dict, last: bool) -> None:
        """Send the audio buffered so far in this turn to the phone as one WAV segment."""
        if not t["buf"] and not last:
            return
        head = self.pending[0] if self.pending else {"kind": "event"}
        first = t["segments"] == 0
        msg = {"type": "voice", "turn": t["id"], "seq": t["segments"], "first": first, "last": last,
               # a new answer or an urgent warning cuts off whatever is playing; later segments queue
               "interrupt": first and (head["kind"] == "talk" or bool(head.get("urgent")))}
        if t["buf"]:
            msg["url"] = f"/api/agent/clip/{self._store(bytes(t['buf']))}"
        t["buf"] = bytearray()
        t["segments"] += 1
        if "url" not in msg and not last:
            return  # nothing new to play; a final marker without audio still tells the phone the turn ended
        if self.sink is not None:
            try:
                self.sink(msg)
                return
            except Exception:  # noqa: BLE001 - phone gone; keep it for the next one
                log.warning("voice push failed", exc_info=True)
        self.outbox.append(msg)

    def _take_pending(self, t: dict) -> dict:
        """The request this turn answers. A turn that heard the user answers the oldest talk; event
        turns it overtook (cut off by the user speaking) are dropped."""
        if t["heard"].strip():
            for i, p in enumerate(self.pending):
                if p["kind"] == "talk":
                    for _ in range(i + 1):
                        q = self.pending.popleft()
                    return q
        return self.pending.popleft() if self.pending else {"kind": "event", "urgent": False}

    def _finish_turn(self) -> None:
        t, self._turn = self._turn, self._new_turn()
        if not (t["audio"] or t["heard"] or t["said"] or t["tools"]) and not self.pending:
            return  # stray empty turn marker
        p = self._take_pending(t)
        out = {"turn": t["id"], "heard": t["heard"].strip(), "reply": t["said"].strip(), "tools": t["tools"]}
        if t["audio"]:
            clip_id = self._store(bytes(t["audio"]))  # whole turn, for the talk response and replay
            out.update(clip=clip_id, url=f"/api/agent/clip/{clip_id}")
        self.state.write_log({"t": round(self.state.t, 3), "kind": p["kind"], "event": p.get("text"), **out})
        if p["kind"] == "talk" and not p["future"].done():
            p["future"].set_result(out)

    # -- inputs ----------------------------------------------------------------
    async def talk(self, pcm16k: bytes) -> dict:
        from google.genai import types

        s = await self.ensure()
        fut = asyncio.get_running_loop().create_future()
        t0 = time.perf_counter()
        async with self._send_lock:
            self.pending.append({"kind": "talk", "future": fut})
            # The view, place and goal go into the conversation first, without asking for a reply.
            await s.send_client_content(turns={"role": "user", "parts": [{"text": self.state.context_line()}]},
                                        turn_complete=False)
            await s.send_realtime_input(activity_start=types.ActivityStart())
            for i in range(0, len(pcm16k), 32000):
                await s.send_realtime_input(audio=types.Blob(data=pcm16k[i:i + 32000], mime_type="audio/pcm;rate=16000"))
            await s.send_realtime_input(activity_end=types.ActivityEnd())
        out = await asyncio.wait_for(fut, TALK_TIMEOUT_S)
        return {**out, "ms": round(1000 * (time.perf_counter() - t0))}

    async def event(self, text: str, urgent: bool = False) -> None:
        try:
            s = await self.ensure()
            async with self._send_lock:
                self.pending.append({"kind": "event", "urgent": urgent, "text": text})
                await s.send_client_content(turns={"role": "user", "parts": [{"text": f"EVENT {text}"}]},
                                            turn_complete=True)
        except Exception:  # noqa: BLE001 - an event must never break the frame pipeline
            log.exception("Live event failed")

    def _emit_threadsafe(self, text: str, urgent: bool) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.event(text, urgent), loop)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def take_outbox(self) -> list[dict]:
        out = []
        while self.outbox:
            out.append(self.outbox.popleft())
        return out

    def describe(self, jpeg: bytes) -> str:
        from google.genai import types

        r = self.client().models.generate_content(model=VISION_MODEL, config=types.GenerateContentConfig(
            http_options=types.HttpOptions(timeout=VISION_TIMEOUT_MS)), contents=[types.Content(role="user", parts=[
            types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
            types.Part.from_text(text="In one short sentence for a blind person: what is in front of the camera? "
                                      "Mention doors, signs and obstacles.")])])
        return (r.text or "").strip()


def agent_router(get_agent) -> APIRouter:
    """get_agent() -> LiveAgent (or None while the server is starting)."""
    router = APIRouter(prefix="/api/agent")
    turn_lock = asyncio.Lock()

    @router.get("/health")
    async def health():
        import shutil
        agent = get_agent()
        return {"ready": agent is not None, "key_configured": bool(read_env_key("GOOGLE_API_KEY", "GEMINI_API_KEY")),
                "model": agent.model if agent else None, "ffmpeg_available": shutil.which("ffmpeg") is not None}

    def need():
        agent = get_agent()
        if agent is None:
            raise HTTPException(503, "Agent not ready")
        return agent

    @router.post("/talk")
    async def talk(request: Request):
        """The clip as the raw request body (the phone) or a multipart `audio` field (curl, tests)."""
        agent = need()
        if turn_lock.locked():
            raise HTTPException(409, "A voice turn is already running")
        kind = request.headers.get("content-type", "")
        # Read at most MAX_AUDIO_BYTES + 1: an oversize clip is refused without being held in memory.
        if kind.startswith("multipart/form-data"):
            part = (await request.form()).get("audio")
            if part is None or not hasattr(part, "read"):
                raise HTTPException(422, "Missing audio field")
            data = await part.read(MAX_AUDIO_BYTES + 1)
        else:
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_AUDIO_BYTES:
                    raise HTTPException(413, "Audio clip empty or too large")
                chunks.append(chunk)
            data = b"".join(chunks)
        log.info("talk request: %d bytes (%s)", len(data), kind or "no content type")
        if not data or len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413 if data else 422, "Audio clip empty or too large")
        # One voice turn at a time: Live pairs each answer with the oldest request, so two at once
        # would answer each other's questions.
        async with turn_lock:
            try:
                pcm = await asyncio.to_thread(to_pcm16k, data)
            except (OSError, subprocess.SubprocessError) as ex:
                raise HTTPException(422, "Audio clip could not be decoded") from ex
            agent.state.quiet = True  # normal guidance must not compete with this answer
            try:
                return await asyncio.wait_for(agent.talk(pcm), timeout=45)
            except Exception as ex:  # noqa: BLE001 - the phone always gets an answer
                log.exception("agent turn failed")
                await agent.close()
                msg = str(ex)
                return {"heard": "", "reply": "", "tools": [], "error": type(ex).__name__,
                        "detail": "out of credit" if ("RESOURCE_EXHAUSTED" in msg or "402" in msg) else msg[:200]}
            finally:
                agent.state.quiet = False

    @router.post("/quiet")
    async def quiet(on: bool = True):
        """The phone calls this when the talk button goes down (on) and up (off)."""
        need().state.quiet = on
        return {"quiet": on}

    @router.post("/nav")
    async def nav(on: int = 1):
        """The phone's Exit/Start button: guidance off (on=0) or on to the entrance from the decided place."""
        state = need().state
        with state.lock:
            if not on:
                out = state.guide.stop()
            else:
                # Same as the start_navigation tool: plan from the place decided right now.
                # Resume toward the current destination (a dropped pin stays the destination).
                out = state.tool("start_navigation", {"destination": "pin" if state.map.goal_kind == "pin" else "entrance"})
            return {**out, "active": state.guide.active}

    @router.post("/venue")
    async def venue_destination(request: Request):
        """Body {"to": "A98", "prefer"?: "stairs"|"elevator"} guides to a venue room; {"to": null} clears."""
        try:
            body = await request.json()
        except ValueError as ex:
            raise HTTPException(422, "JSON body required") from ex
        to = body.get("to")
        return need().state.set_venue_destination(None if to in (None, "") else str(to), body.get("prefer"))

    @router.get("/destination")
    async def destination_preview(x: float, y: float):
        """Preview a pin: where it lands (moved if unreachable) and the route length, no change."""
        return need().state.preview_destination(x, y)

    @router.post("/destination")
    async def destination_set(request: Request):
        """Body {"x": .., "y": .., "label"?: ..} drops a pin and guides there; {"entrance": true}
        goes back to the entrance."""
        try:
            body = await request.json()
        except ValueError as ex:
            raise HTTPException(422, "JSON body required") from ex
        state = need().state
        if body.get("entrance"):
            return state.set_destination(None)
        try:
            x, y = float(body["x"]), float(body["y"])
        except (KeyError, TypeError, ValueError) as ex:
            raise HTTPException(422, "x and y (metres, map frame) required") from ex
        if not (math.isfinite(x) and math.isfinite(y)):
            raise HTTPException(422, "x and y must be finite")
        label = str(body.get("label") or "your pin")[:40]
        return state.set_destination(x, y, label)

    @router.get("/clip/{clip_id}")
    async def clip(clip_id: str, request: Request):
        data = need().clips.get(clip_id)
        if data is None:
            raise HTTPException(404, "Clip not found")
        return wav_response(data, request.headers.get("range"))

    @router.get("/state")
    async def state():
        return need().state.snapshot()

    @router.post("/reset")
    async def reset():
        agent = need()
        await agent.reset()
        agent.state.reset()
        return {"ok": True}

    return router
