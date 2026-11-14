import test from "node:test";
import assert from "node:assert/strict";
import { AUDIO_MODE, SPEECH_OPTIONS, speechOptions } from "../../src/audio.js";

test("audio plays even when the iPhone silent switch is on", () => {
  assert.equal(AUDIO_MODE.playsInSilentMode, true);
});

test("speech ducks other audio instead of being drowned out by it", () => {
  assert.equal(AUDIO_MODE.interruptionMode, "duckOthers");
  assert.equal(AUDIO_MODE.allowsRecording, false);
});

test("speech is full volume, English, at a clear pace", () => {
  assert.equal(SPEECH_OPTIONS.volume, 1);
  assert.equal(SPEECH_OPTIONS.language, "en-US");
  assert.ok(SPEECH_OPTIONS.rate >= 0.9 && SPEECH_OPTIONS.rate <= 1.1);
  assert.equal(SPEECH_OPTIONS.useApplicationAudioSession, true);
});

test("speechOptions merges lifecycle callbacks without losing the defaults", () => {
  const calls = [];
  const opts = speechOptions({ onStart: () => calls.push("start"), onDone: () => calls.push("done") });
  assert.equal(opts.volume, 1);
  opts.onStart();
  opts.onDone();
  assert.deepEqual(calls, ["start", "done"]);
});

test("every way speech can end clears the speaking flag", () => {
  let speaking = true;
  const opts = speechOptions({ onSettled: () => { speaking = false; } });
  for (const end of ["onDone", "onStopped", "onError"]) {
    speaking = true;
    opts[end]();
    assert.equal(speaking, false, end);
  }
});
