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
from fastapi.responses import Response

from server.cues import HAZARDS
from server.entrance_nav import EntranceGuide, EntranceMap, describe_route, plan, route_sentence, start_point
from server.heading import HeadingKalman, wrap180
from server.route import TURN_DEG, clock
from server.place_window import PlaceWindow
from server.scene_memory import SceneMemory
from server.voice import pcm_to_wav, wav_response

log = logging.getLogger("blindsight.agent")

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
AUTO_NAV = os.environ.get("AUTO_NAV", "1") != "0"  # guide to the entrance without being asked
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

SYSTEM_PROMPT = """You are Blindsight: a warm, calm companion walking beside a blind person indoors, talking
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
- Always give distances in the person's steps, never in meters: they can count steps but cannot judge
  meters without sight. Tool results carry steps (…_steps fields); if one only has meters, convert at
  about 0.7 m per step.

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
- The CONTEXT line always carries the path from where they stand to the entrance, worked out from
  their location and the way they face. When they ask how to get out, how far it is or which way to go,
  explain it like a person would ("It's about seventeen steps. Turn to your right, walk about seven steps,
  then a little left and straight to the door."). plan_route gives the same path fresh. Guidance to
  the entrance runs on its own from the start; if they said stop and later want to go, use
  start_navigation.
- "Stop" / "never mind": stop_navigation. "How far?": get_route_status or the context.
- Only say guidance started or stopped if the tool result says ok."""

TOOLS = [
    ("get_location", "Where the user is: decided place, confidence, tracked position.", {}),
    ("get_surroundings", "Objects in the current camera frame and ones seen in the last minute.", {}),
    ("start_navigation", "Start spoken turn-by-turn guidance to a destination.",
     {"destination": ("STRING", "Destination; only 'entrance' is supported.")}),
    ("stop_navigation", "Stop the spoken guidance.", {}),
    ("get_route_status", "Progress of the current guidance: next point, meters left.", {}),
    ("get_direction", "Which way to go right now: the turn to make from the way the person faces and how far to "
                      "walk, plus how sure the facing direction is. Call it whenever they ask which way to go.", {}),
    ("plan_route", "The whole path from where the person stands to the entrance: total meters and each "
                   "straight leg with the turn before it (first turn relative to where they face).", {}),
    ("describe_view", "Describe the latest camera image in one sentence.", {}),
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
        had_route = self.guide.route is not None
        nav = self.guide.update(t, self.decision.place, self.decision.state, self.yaw_deg, self.position,
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
        for text, urgent in events:
            if emit is not None:
                emit(text, urgent)

    def on_frame(self, t: float, match: dict | None, described: list[dict], location: dict,
                 jpeg: bytes | None = None, compass_deg: float | None = None,
                 moving: bool | None = None, steps: int | None = None,
                 ts_ms: float | None = None) -> list[tuple[str, bool]]:
        """Update from one processed frame. Returns (and emits) the events to speak: (text, urgent).
        ts_ms is the frame's capture time on the phone clock (see on_imu)."""
        with self.lock:
            self._received_at = time.monotonic()
            self.t = t
            match = match or {}
            ranking = match.get("ranking") if location.get("state", "LOCKED") == "LOCKED" else None
            self.decision = self.window.push(t, ranking)
            self.position = match.get("position")
            place = self.decision.place
            # The localizer's yaw belongs to the place it reports; use it only when that is ours.
            self.yaw_deg = location.get("yaw_deg") if location.get("pano_id") == place else None
            # Fused heading (PnP > matched view > calibrated compass), mesh frame; see server/heading.py.
            self._fuse_frame_heading(t, match, compass_deg, moving, ts_ms)
            self.memory.update(t, described, place)
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

    def direction(self) -> dict:
        """Which way to go right now: the next turn and its distance along the route (the guide's
        route while guiding, a fresh one otherwise), plus heading info."""
        with self.lock:
            d, est = self.decision, dict(self.heading.estimate)
            guided = self.guide.active and self.guide.route is not None
            nav = dict(self.guide.status)
            fresh = None if guided else describe_route(self.map, d.place, self.yaw_deg, self.position,
                                                       est.get("heading_deg"), stride=self.guide.stride)
        if guided:
            if "next_m" not in nav:
                return {"ok": False, "say": "Hold the phone steady and I'll find the path."}
            turn = nav.get("turn_deg")
            do_now = ("go straight" if turn is None or abs(turn) <= TURN_DEG else
                      f"turn {'right' if turn > 0 else 'left'} to your {clock(turn)} o'clock")
            out = {"ok": True, "do_now": do_now, "then_walk_m": nav["next_m"], "next_turn": nav["next"],
                   "next_turn_in_m": nav["next_m"], "remaining_m": nav["remaining_m"],
                   "next_turn_in_steps": nav.get("next_steps"), "remaining_steps": nav.get("remaining_steps")}
        else:
            if not fresh.get("ok"):
                return {**fresh, "say": "Hold the phone steady and I'll find the path."}
            if fresh.get("at_destination"):
                return {"ok": True, "at_destination": True}
            leg = fresh["legs"][0]
            out = {"ok": True, "do_now": leg["do"], "then_walk_m": leg["meters"], "then_walk_steps": leg.get("steps"), "next_turn": fresh["next_turn"],
                   "next_turn_in_m": fresh["next_turn_in_m"], "remaining_m": fresh["total_m"]}
        out.update(heading_source=est.get("source"), heading_accuracy_deg=est.get("sigma_deg"))
        if est.get("heading_deg") is None:
            out["note"] = "facing direction unknown: ask them to hold the phone up, facing forward, and turn slowly"
        elif (est.get("sigma_deg") or 0) >= 20:
            out["note"] = "facing direction is rough; say the direction as approximate"
        return out

    def context_line(self) -> str:
        """What the agent should know when the person speaks: view, recent objects, place, goal."""
        with self.lock:
            d, mem, nav = self.decision, self.memory.summary(self.t, self.place_label), dict(self.guide.status)
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
            route = route_sentence(describe_route(self.map, d.place, self.yaw_deg, self.position, self.heading.estimate.get("heading_deg"), stride=self.guide.stride))
        return f"CONTEXT: Path to the entrance from here: {route}. In view now: {now}. Seen in the last minute: {recent or 'nothing'}. Place: {place}. Facing direction: {facing}. Goal: {goal}."

    def snapshot(self) -> dict:
        with self.lock:
            self.decision = self.window.decide(self.t + time.monotonic() - self._received_at)
            est = self.heading.estimate
            heading = {"heading_deg": est.get("heading_deg"), "sigma_deg": est.get("sigma_deg"),
                       "source": est.get("source"),
                       # raw phone compass (magnetic/true north, not the map frame), for comparison
                       "compass_deg": self._compass_buf[-1][1] if self._compass_buf else None}
            return {"decision": self.decision.to_dict(), "place_label": self.place_label(self.decision.place),
                    "nav": self.guide.status, "heading": heading, "path": self._path_view()}

    def _path_view(self) -> dict:
        """The route for the viewer (map frame, metres): the one being guided, else a preview planned
        from the decided place and tracked position. Plus the entrance, for a straight-line comparison."""
        route, guiding = self.guide.route, self.guide.route is not None
        if route is None and self.decision.place in self.map.points:
            route = plan(self.map, *start_point(self.map, self.decision.place, self.position))
        goal = self.map.points.get(self.map.goal)
        return {"pts": [[round(x, 2), round(y, 2)] for x, y in route.pts] if route else None,
                "guiding": guiding, "length_m": round(route.length, 1) if route else None,
                "goal": [round(goal[0], 2), round(goal[1], 2)] if goal else None}

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
                dest = str(args.get("destination") or "entrance").lower()
                if "entr" not in dest and "door" not in dest and "exit" not in dest and "out" not in dest:
                    return {"ok": False, "reason": "only the entrance is supported"}
                return self.guide.start(d.place)
            if name == "stop_navigation":
                return self.guide.stop()
            if name == "get_route_status":
                return dict(self.guide.status)
            if name == "get_direction":
                return self.direction()
            if name == "plan_route":
                return describe_route(self.map, d.place, self.yaw_deg, self.position, self.heading.estimate.get("heading_deg"), stride=self.guide.stride)
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
            agent.state.quiet = False
            try:
                return await asyncio.wait_for(agent.talk(pcm), timeout=45)
            except Exception as ex:  # noqa: BLE001 - the phone always gets an answer
                log.exception("agent turn failed")
                await agent.close()
                msg = str(ex)
                return {"heard": "", "reply": "", "tools": [], "error": type(ex).__name__,
                        "detail": "out of credit" if ("RESOURCE_EXHAUSTED" in msg or "402" in msg) else msg[:200]}

    @router.post("/quiet")
    async def quiet(on: bool = True):
        """The phone calls this when the talk button goes down (on) and up (off)."""
        need().state.quiet = on
        return {"quiet": on}

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
