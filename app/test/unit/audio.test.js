import test from "node:test";
import assert from "node:assert/strict";
import { AUDIO_MODE } from "../../src/audio.js";

test("audio plays even when the iPhone silent switch is on", () => {
  assert.equal(AUDIO_MODE.playsInSilentMode, true);
});

test("speech ducks other audio instead of being drowned out by it", () => {
  assert.equal(AUDIO_MODE.interruptionMode, "duckOthers");
  assert.equal(AUDIO_MODE.allowsRecording, false);
});
