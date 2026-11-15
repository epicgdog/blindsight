"""Fixed app phrases as Gemini TTS audio (the Live agent speaks everything else).

GET /api/voice?text=... returns a WAV of that text, made by gemini-2.5-flash-preview-tts
(v1beta; 24 kHz 16-bit mono PCM wrapped as WAV). Every phrase is cached on disk under
data/voice_cache/ and in memory, so a repeated cue plays at once; a new phrase takes about
2 s. Fixed phrases (guidance, status) are made in advance at startup.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import threading
import wave
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

log = logging.getLogger("insight.voice")

_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


def wav_response(data: bytes, range_header: str | None, headers: dict | None = None) -> Response:
    """Audio bytes with HTTP Range support. iOS AVPlayer probes with `Range: bytes=0-1` and
    refuses media ("Operation Stopped") unless the server answers 206 Partial Content."""
    h = {"Accept-Ranges": "bytes", **(headers or {})}
    n = len(data)
    m = _RANGE.match((range_header or "").strip())
    if not m or (not m.group(1) and not m.group(2)):
        return Response(data, media_type="audio/wav", headers=h)
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)) if m.group(2) else n - 1, n - 1)
    else:  # suffix range: last N bytes
        start, end = max(n - int(m.group(2)), 0), n - 1
    if start >= n or start > end:
        return Response(status_code=416, headers={**h, "Content-Range": f"bytes */{n}"})
    h["Content-Range"] = f"bytes {start}-{end}/{n}"
    return Response(data[start:end + 1], status_code=206, media_type="audio/wav", headers=h)

TTS_MODEL = "gemini-2.5-flash-preview-tts"
VOICE_NAME = "Kore"
MAX_TEXT = 300
MEMORY_ITEMS = 300
STYLE = "Say clearly and calmly, at a brisk pace: "


def warm_phrases() -> list[str]:
    """Fixed app phrases (everything else is spoken by the Live agent)."""
    return ["Insight ready", "Microphone not available.", "I can't reach the voice assistant.",
            "Sorry, the voice assistant had a problem."]


def pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _rate(mime: str) -> int:
    m = re.search(r"rate=(\d+)", mime or "")
    return int(m.group(1)) if m else 24000


class GeminiVoice:
    def __init__(self, cache_dir: Path | None, client=None, model: str = TTS_MODEL, voice: str = VOICE_NAME):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.model, self.voice = model, voice
        self._client = client
        self._mem: OrderedDict[str, bytes] = OrderedDict()
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}

    def client(self):
        if self._client is None:
            from google import genai
            from google.genai import types

            from server.agent import read_env_key

            key = read_env_key("GOOGLE_API_KEY", "GEMINI_API_KEY")
            if not key:
                raise RuntimeError("GOOGLE_API_KEY missing (environment or .env.local)")
            # TTS models and speech_config are only on v1beta.
            self._client = genai.Client(api_key=key, http_options=types.HttpOptions(api_version="v1beta"))
        return self._client

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha1(f"{TTS_MODEL}|{VOICE_NAME}|{text}".encode()).hexdigest()[:20]

    def _synthesize(self, text: str) -> bytes:
        from google.genai import types

        cfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice))))
        r = self.client().models.generate_content(model=self.model, contents=STYLE + text, config=cfg)
        for part in r.candidates[0].content.parts:
            if part.inline_data and part.inline_data.data:
                return pcm_to_wav(part.inline_data.data, _rate(part.inline_data.mime_type))
        raise RuntimeError("TTS returned no audio")

    def wav(self, text: str) -> bytes:
        text = " ".join(text.split())[:MAX_TEXT]
        if not text:
            raise ValueError("empty text")
        k = self.key(text)
        while True:
            with self._lock:
                if k in self._mem:
                    self._mem.move_to_end(k)
                    return self._mem[k]
                path = self.cache_dir / f"{k}.wav" if self.cache_dir else None
                if path is not None and path.is_file():
                    data = path.read_bytes()
                    self._remember(k, data)
                    return data
                ev = self._inflight.get(k)
                if ev is None:
                    ev = self._inflight[k] = threading.Event()
                    owner = True
                else:
                    owner = False
            if not owner:
                ev.wait(30)  # same phrase already being made: wait for it, then read the cache
                continue
            try:
                data = self._synthesize(text)
                with self._lock:
                    self._remember(k, data)
                if path is not None:
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                    except OSError:
                        log.warning("voice cache write failed", exc_info=True)
                return data
            finally:
                with self._lock:
                    self._inflight.pop(k, None)
                ev.set()

    def _remember(self, k: str, data: bytes) -> None:
        self._mem[k] = data
        self._mem.move_to_end(k)
        while len(self._mem) > MEMORY_ITEMS:
            self._mem.popitem(last=False)

    def warm(self, phrases=None) -> int:
        """Synthesize phrases not cached yet (run on a background thread). Returns how many."""
        made = 0
        for text in phrases or warm_phrases():
            path = self.cache_dir / f"{self.key(text)}.wav" if self.cache_dir else None
            if path is not None and path.is_file():
                continue
            try:
                self.wav(text)
                made += 1
            except Exception as ex:  # noqa: BLE001 - warming is best effort
                log.warning("voice warm-up stopped: %s", str(ex)[:160])
                break
        return made


def voice_router(get_voice) -> APIRouter:
    import asyncio

    router = APIRouter()

    @router.get("/api/voice")
    async def voice(request: Request, text: str = Query(min_length=1, max_length=MAX_TEXT)):
        v = get_voice()
        if v is None:
            raise HTTPException(503, "Voice not ready")
        try:
            data = await asyncio.to_thread(v.wav, text)
        except ValueError as ex:
            raise HTTPException(422, str(ex)) from ex
        except Exception as ex:  # noqa: BLE001
            log.exception("TTS failed")
            raise HTTPException(502, f"TTS failed: {type(ex).__name__}") from ex
        return wav_response(data, request.headers.get("range"), {"Cache-Control": "public, max-age=86400"})

    return router
