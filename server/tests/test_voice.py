"""Gemini TTS cache and the /api/voice endpoint (no network: synthesis is faked)."""

import io
import threading
import time
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.voice as voice
from server.voice import GeminiVoice, _rate, pcm_to_wav, voice_router, warm_phrases

WAV = pcm_to_wav(b"\x01\x00" * 200)


class Synth:
    """Replaces GeminiVoice._synthesize and records the texts it was asked for."""

    def __init__(self, fail_on=None):
        self.calls, self.fail_on = [], fail_on

    def __call__(self, text):
        self.calls.append(text)
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("TTS down")
        return WAV


def make(tmp_path, synth=None):
    v = GeminiVoice(tmp_path / "cache")
    v._synthesize = synth or Synth()
    return v


# ---- audio helpers --------------------------------------------------------------------
def test_pcm_to_wav_is_mono_16bit_at_the_given_rate():
    data = pcm_to_wav(b"\x00\x00" * 50, rate=16000)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, 16000, 50)


def test_rate_is_read_from_the_mime_type_with_a_default():
    assert _rate("audio/L16;codec=pcm;rate=16000") == 16000
    assert _rate("audio/pcm") == 24000
    assert _rate(None) == 24000


# ---- GeminiVoice ----------------------------------------------------------------------
def test_a_phrase_is_synthesized_once_then_served_from_memory(tmp_path):
    synth = Synth()
    v = make(tmp_path, synth)
    assert v.wav("Hello there") == WAV
    assert v.wav("Hello there") == WAV
    assert synth.calls == ["Hello there"]


def test_whitespace_is_normalized_so_it_shares_one_cache_entry(tmp_path):
    synth = Synth()
    v = make(tmp_path, synth)
    v.wav("  hello   there ")
    v.wav("hello there")
    assert len(synth.calls) == 1


def test_the_disk_cache_survives_a_new_instance(tmp_path):
    first = Synth()
    make(tmp_path, first).wav("Blindsight ready")
    second = Synth()
    assert make(tmp_path, second).wav("Blindsight ready") == WAV
    assert second.calls == []


def test_empty_text_is_refused(tmp_path):
    with pytest.raises(ValueError):
        make(tmp_path).wav("   ")


def test_long_text_is_cut_to_the_limit(tmp_path):
    synth = Synth()
    make(tmp_path, synth).wav("x" * (voice.MAX_TEXT + 50))
    assert len(synth.calls[0]) == voice.MAX_TEXT


def test_the_memory_cache_is_bounded_but_the_disk_still_has_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "MEMORY_ITEMS", 2)
    synth = Synth()
    v = make(tmp_path, synth)
    for t in ("one", "two", "three"):
        v.wav(t)
    assert len(v._mem) == 2
    v.wav("one")  # evicted from memory, read back from disk
    assert synth.calls == ["one", "two", "three"]


def test_the_cache_key_depends_on_the_text():
    assert GeminiVoice.key("a") == GeminiVoice.key("a")
    assert GeminiVoice.key("a") != GeminiVoice.key("b")


def test_concurrent_requests_for_one_phrase_synthesize_once(tmp_path):
    started, release, calls = threading.Event(), threading.Event(), []

    def slow(text):
        calls.append(text)
        started.set()
        release.wait(5)
        return WAV

    v = make(tmp_path)
    v._synthesize = slow
    out = []
    threads = [threading.Thread(target=lambda: out.append(v.wav("same phrase"))) for _ in range(4)]
    threads[0].start()
    assert started.wait(5)
    for t in threads[1:]:
        t.start()
    time.sleep(0.1)
    release.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1
    assert out == [WAV] * 4


def test_a_failed_synthesis_is_not_cached_and_can_be_retried(tmp_path):
    synth = Synth(fail_on="boom")
    v = make(tmp_path, synth)
    with pytest.raises(RuntimeError):
        v.wav("boom")
    synth.fail_on = None
    assert v.wav("boom") == WAV
    assert len(synth.calls) == 2


def test_warm_makes_only_phrases_that_are_not_cached(tmp_path):
    synth = Synth()
    v = make(tmp_path, synth)
    v.wav(warm_phrases()[0])
    synth.calls.clear()
    assert v.warm() == len(warm_phrases()) - 1
    assert warm_phrases()[0] not in synth.calls
    assert v.warm() == 0


def test_warm_stops_quietly_on_the_first_error(tmp_path):
    phrases = ["first", "second", "third"]
    v = make(tmp_path, Synth(fail_on="second"))
    assert v.warm(phrases) == 1  # "first" was made; the error stops the rest without raising


# ---- /api/voice -----------------------------------------------------------------------
def client_for(get_voice):
    app = FastAPI()
    app.include_router(voice_router(get_voice))
    return TestClient(app)


def test_voice_endpoint_serves_wav_with_caching_headers(tmp_path):
    c = client_for(lambda: make(tmp_path))
    r = c.get("/api/voice", params={"text": "Hello"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == WAV
    assert "max-age" in r.headers["cache-control"]


def test_voice_endpoint_supports_byte_ranges_for_ios(tmp_path):
    c = client_for(lambda: make(tmp_path))
    r = c.get("/api/voice", params={"text": "Hello"}, headers={"Range": "bytes=0-1"})
    assert r.status_code == 206
    assert r.content == WAV[:2]


def test_voice_endpoint_is_503_until_the_voice_exists(tmp_path):
    assert client_for(lambda: None).get("/api/voice", params={"text": "Hello"}).status_code == 503


def test_voice_endpoint_rejects_blank_text(tmp_path):
    c = client_for(lambda: make(tmp_path))
    assert c.get("/api/voice", params={"text": ""}).status_code == 422
    assert c.get("/api/voice", params={"text": "   "}).status_code == 422


def test_voice_endpoint_reports_a_tts_failure_as_502(tmp_path):
    c = client_for(lambda: make(tmp_path, Synth(fail_on="boom")))
    r = c.get("/api/voice", params={"text": "boom"})
    assert r.status_code == 502
    assert "RuntimeError" in r.json()["detail"]
