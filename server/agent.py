"""Hold-to-talk voice agent (demo): Gemini hears the clip, calls tools, replies in text.

The phone records a clip and POSTs it to /api/agent/talk; the reply text comes back and the
phone speaks it. Location is never the model's guess: the place comes from PlaceWindow, the
route and turn instructions from EntranceGuide, objects from SceneMemory. Gemini only picks
what to do from the conversation (start or stop guidance, answer "where am I", ...).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from server.entrance_nav import EntranceGuide, EntranceMap
from server.place_window import PlaceWindow
from server.scene_memory import SceneMemory

log = logging.getLogger("blindsight.agent")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "gemini-2.5-flash"
ENTRANCE_PANO = "n0ppizy9u8xcadypd2bawceyc"  # scan 28, also ENTRANCE_ID in server/viewer/logic.js
HISTORY_TURNS = 10
MAX_TOOL_ROUNDS = 4
MAX_AUDIO_BYTES = 5_000_000

SYSTEM_PROMPT = """You are Blindsight, a voice guide for a blind person walking indoors on one floor of a building.
You hear one short voice message at a time. Use the tools; never guess a location, an object or a direction.
- Where they are: get_location. If it says unknown, say you are not sure yet and ask them to look around slowly.
- What is around: get_surroundings (objects now and in the last minute).
- "Take me to the entrance" / "get me out" / "where is the door": start_navigation. Turn-by-turn
  instructions are then spoken automatically; do not invent turns or distances yourself.
- "Stop" / "cancel": stop_navigation. Progress questions: get_route_status.
- describe_view only when they ask what the camera sees in more detail.
Replies are spoken aloud: one or two short sentences, no lists, no markdown.
Format every final answer exactly as:
HEARD: <what the person said, verbatim>
REPLY: <your spoken reply>"""

TOOLS = [
    ("get_location", "Where the user is: decided place, confidence, tracked position.", {}),
    ("get_surroundings", "Objects in the current camera frame and ones seen in the last minute.", {}),
    ("start_navigation", "Start spoken turn-by-turn guidance to a destination.",
     {"destination": ("STRING", "Destination; only 'entrance' is supported.")}),
    ("stop_navigation", "Stop the spoken guidance.", {}),
    ("get_route_status", "Progress of the current guidance: next point, meters left.", {}),
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


def to_wav(audio: bytes) -> tuple[bytes, str]:
    """Phone clips (m4a/AAC) -> 16 kHz mono WAV with ffmpeg; pass through if ffmpeg fails."""
    try:
        out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000",
                              "-f", "wav", "pipe:1"], input=audio, capture_output=True, timeout=20, check=True)
        return out.stdout, "audio/wav"
    except (OSError, subprocess.SubprocessError):
        return audio, "audio/mp4"


def parse_reply(text: str) -> tuple[str, str]:
    heard, reply = "", (text or "").strip()
    if "REPLY:" in reply:
        head, reply = reply.split("REPLY:", 1)
        heard = head.split("HEARD:", 1)[-1].strip()
        reply = reply.strip()
    elif reply.startswith("HEARD:"):
        heard, _, reply = reply[len("HEARD:"):].partition("\n")
        heard, reply = heard.strip(), reply.strip()
    return heard, reply or "Sorry, I didn't catch that."


class AgentState:
    """Per-frame world state the agent reads: decided place, objects, guidance. Thread safe."""

    def __init__(self, data_dir: Path, entrance: str | None = ENTRANCE_PANO, log_dir: Path | None = None,
                 place_label=None):
        self.data_dir = Path(data_dir)
        self.lock = threading.RLock()
        self.window = PlaceWindow()
        self.memory = SceneMemory()
        self.map = EntranceMap.load(self.data_dir, entrance)
        self.guide = EntranceGuide(self.map)
        self.place_label = place_label or self.map.label
        self.decision = self.window.decide(0.0)
        self.position: dict | None = None
        self.yaw_deg: float | None = None
        self.jpeg: bytes | None = None
        self.t = 0.0
        self.log_dir = log_dir
        self.session = None
        self._received_at = time.monotonic()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.window.reset()
            self.memory.reset()
            self.guide.stop()
            self.decision = self.window.decide(0.0)
            self.position = self.yaw_deg = self.jpeg = None
            self._received_at = time.monotonic()
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

    def on_frame(self, t: float, match: dict | None, described: list[dict], location: dict,
                 jpeg: bytes | None = None) -> str | None:
        """Update from one processed frame; returns a guidance instruction to speak, or None."""
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
            self.memory.update(t, described, place)
            if jpeg is not None:
                self.jpeg = jpeg
            text = self.guide.update(t, place, self.decision.state, self.yaw_deg, self.position)
            self.write_log({"t": round(t, 3), "kind": "frame", "decision": self.decision.to_dict(),
                            "top3": (match.get("ranking") or [])[:3], "objects": self.memory.summary(t)["now"],
                            "position": self.position, "yaw_deg": self.yaw_deg, "nav": self.guide.status,
                            "nav_text": text})
            return text

    def snapshot(self) -> dict:
        with self.lock:
            self.decision = self.window.decide(self.t + time.monotonic() - self._received_at)
            return {"decision": self.decision.to_dict(), "place_label": self.place_label(self.decision.place),
                    "nav": self.guide.status}

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
            jpeg = self.jpeg
        if name == "describe_view":
            if jpeg is None or describe is None:
                return {"ok": False, "reason": "no camera image yet"}
            return {"description": describe(jpeg)}
        return {"ok": False, "reason": f"unknown tool {name}"}


class GeminiAgent:
    """One conversation (text history) driving AgentState tools through Gemini function calls."""

    def __init__(self, state: AgentState, model: str | None = None, client=None):
        self.state = state
        self.model = model or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self._client = client
        self.history: list = []  # types.Content, text only

    def client(self):
        if self._client is None:
            from google import genai

            key = read_env_key("GOOGLE_API_KEY", "GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GOOGLE_API_KEY missing (environment or .env.local)")
            self._client = genai.Client(api_key=key, http_options={"timeout": 10000})
        return self._client

    @staticmethod
    def _config():
        from google.genai import types

        decls = []
        for name, desc, params in TOOLS:
            props = {k: types.Schema(type=t, description=dsc) for k, (t, dsc) in params.items()}
            decls.append(types.FunctionDeclaration(
                name=name, description=desc,
                parameters=types.Schema(type="OBJECT", properties=props) if props else None))
        return types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT, tools=[types.Tool(function_declarations=decls)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))

    def describe(self, jpeg: bytes) -> str:
        from google.genai import types

        r = self.client().models.generate_content(model=self.model, contents=[types.Content(role="user", parts=[
            types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
            types.Part.from_text(text="In one short sentence for a blind person: what is in front of the camera? "
                                      "Mention doors, signs and obstacles.")])])
        return (r.text or "").strip()

    def talk(self, audio: bytes, mime: str) -> dict:
        from google.genai import types

        t0 = time.perf_counter()
        user = types.Content(role="user", parts=[types.Part.from_bytes(data=audio, mime_type=mime),
                                                 types.Part.from_text(text="(voice message)")])
        contents = [*self.history, user]
        calls, config = [], self._config()
        resp = None
        for _ in range(MAX_TOOL_ROUNDS):
            resp = self.client().models.generate_content(model=self.model, contents=contents, config=config)
            fcs = resp.function_calls or []
            if not fcs:
                break
            contents.append(resp.candidates[0].content)
            parts = []
            for fc in fcs:
                result = self.state.tool(fc.name, dict(fc.args or {}), describe=self.describe)
                calls.append({"name": fc.name, "args": dict(fc.args or {}), "result": result})
                parts.append(types.Part.from_function_response(name=fc.name, response=result))
            contents.append(types.Content(role="user", parts=parts))
        heard, reply = parse_reply(resp.text if resp is not None and not resp.function_calls else "")
        self.history += [types.Content(role="user", parts=[types.Part.from_text(text=heard or "(unclear audio)")]),
                         types.Content(role="model", parts=[types.Part.from_text(text=reply)])]
        self.history = self.history[-2 * HISTORY_TURNS:]
        out = {"heard": heard, "reply": reply, "tools": calls, "ms": round(1000 * (time.perf_counter() - t0))}
        self.state.write_log({"t": round(self.state.t, 3), "kind": "talk", **out})
        return out

    def reset(self) -> None:
        self.history = []


def agent_router(get_agent) -> APIRouter:
    """get_agent() -> GeminiAgent (or None while the server is starting)."""
    import asyncio

    router = APIRouter(prefix="/api/agent")
    turn_lock = asyncio.Lock()

    @router.get("/health")
    async def health():
        import shutil
        agent = get_agent()
        return {"ready": agent is not None, "key_configured": bool(read_env_key("GOOGLE_API_KEY", "GEMINI_API_KEY")),
                "model": agent.model if agent else None, "ffmpeg_available": shutil.which("ffmpeg") is not None}

    @router.post("/talk")
    async def talk(audio: UploadFile = File(...)):
        agent = get_agent()
        if agent is None:
            raise HTTPException(503, "Agent not ready")
        data = await audio.read(MAX_AUDIO_BYTES + 1)
        if not data or len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413 if data else 422, "Audio clip empty or too large")
        if turn_lock.locked():
            raise HTTPException(409, "A voice turn is already running")
        try:
            async with turn_lock:
                wav, mime = await asyncio.to_thread(to_wav, data)
                return await asyncio.to_thread(agent.talk, wav, mime)
        except Exception as ex:  # noqa: BLE001 - the phone must always get something to say
            log.exception("agent turn failed")
            msg = str(ex)
            reply = ("The voice assistant is out of credit." if "RESOURCE_EXHAUSTED" in msg or "402" in msg
                     else "Sorry, the voice assistant is not available right now.")
            return {"heard": "", "reply": reply, "tools": [], "error": type(ex).__name__}

    @router.get("/state")
    async def state():
        agent = get_agent()
        if agent is None:
            raise HTTPException(503, "Agent not ready")
        return agent.state.snapshot()

    @router.post("/reset")
    async def reset():
        agent = get_agent()
        if agent is None:
            raise HTTPException(503, "Agent not ready")
        agent.reset()
        return {"ok": True}

    return router
