import test from "node:test";
import assert from "node:assert/strict";
import { createVoicePlayer, voiceUrl, STALL_MS } from "../../src/voice.js";

test("voice url follows the websocket host and encodes the text", () => {
  assert.equal(voiceUrl("wss://box.ts.net/ws/phone", "Turn right, to your 2 o'clock."),
    "https://box.ts.net/api/voice?text=Turn%20right%2C%20to%20your%202%20o'clock.");
  assert.equal(voiceUrl("ws://10.0.0.2:8000/ws/phone", "hi"), "http://10.0.0.2:8000/api/voice?text=hi");
  assert.equal(voiceUrl("", "hi"), null);
});

function fakePlayer() {
  const p = { calls: [], listener: null };
  p.addListener = (name, fn) => { if (name === "playbackStatusUpdate") p.listener = fn; };
  p.replace = (src) => p.calls.push(["replace", src.uri]);
  p.play = () => p.calls.push(["play"]);
  p.pause = () => p.calls.push(["pause"]);
  return p;
}

// The player is double buffered: two players, so tests hand out two distinct fakes, in order.
function fakePair() {
  const players = [fakePlayer(), fakePlayer()];
  let n = 0;
  return { players, createPlayer: () => players[n++] };
}
const replaced = (p) => p.calls.filter((c) => c[0] === "replace").map((c) => c[1]);

test("speak plays the server audio and reports start and end", () => {
  const { players: [p0], createPlayer } = fakePair();
  const v = createVoicePlayer({ createPlayer, getWsUrl: () => "wss://h/ws/phone" });
  const events = [];
  v.speak("Hello", { onStart: () => events.push("start"), onSettled: () => events.push("end") });
  assert.deepEqual(p0.calls, [["replace", "https://h/api/voice?text=Hello"], ["play"]]);
  assert.equal(v.isSpeaking(), true);
  p0.listener({ playing: true, didJustFinish: false });
  p0.listener({ playing: true, didJustFinish: false }); // start fires once
  p0.listener({ playing: false, didJustFinish: true });
  assert.deepEqual(events, ["start", "end"]);
  assert.equal(v.isSpeaking(), false);
});

test("a new phrase or stop settles the previous one", () => {
  const p = fakePlayer();
  const v = createVoicePlayer({ createPlayer: () => p, getWsUrl: () => "wss://h/ws/phone" });
  const ends = [];
  v.speak("one", { onSettled: () => ends.push("one") });
  v.speak("two", { onSettled: () => ends.push("two") });
  v.stop();
  assert.deepEqual(ends, ["one", "two"]);
  assert.equal(v.isSpeaking(), false);
});

test("no server url: nothing plays and the phrase settles", () => {
  const p = fakePlayer();
  const v = createVoicePlayer({ createPlayer: () => p, getWsUrl: () => "" });
  let ended = false;
  v.speak("hi", { onSettled: () => { ended = true; } });
  assert.equal(ended, true);
  assert.deepEqual(p.calls, []);
});

test("agent clips queue behind the current one, loading ahead in the idle player; urgent clips cut in", () => {
  const { players: [p0, p1], createPlayer } = fakePair();
  const v = createVoicePlayer({ createPlayer, getWsUrl: () => "wss://h/ws/phone" });
  v.playPath("/api/agent/clip/a");
  v.playPath("/api/agent/clip/b");
  assert.deepEqual(replaced(p0), ["https://h/api/agent/clip/a"]);
  assert.deepEqual(p0.calls.filter((c) => c[0] === "play").length, 1); // only a is playing
  assert.deepEqual(replaced(p1), ["https://h/api/agent/clip/b"]); // b preloads in the other player...
  assert.equal(p1.calls.some((c) => c[0] === "play"), false); // ...but does not play yet
  p0.listener({ playing: false, didJustFinish: true });
  assert.deepEqual(replaced(p1), ["https://h/api/agent/clip/b"]); // already loaded: no second replace
  assert.equal(p1.calls.at(-1)[0], "play"); // b starts when a finishes, without a fetch gap
  v.playPath("/api/agent/clip/c");
  assert.deepEqual(replaced(p0).at(-1), "https://h/api/agent/clip/c"); // c preloads in the idle player
  v.playPath("/api/agent/clip/stop", { interrupt: true });
  assert.deepEqual(replaced(p0).at(-1), "https://h/api/agent/clip/stop");
  assert.equal(p1.calls.at(-1)[0], "pause"); // the clip that was playing is cut
  p0.listener({ playing: false, didJustFinish: true });
  assert.equal(v.isSpeaking(), false); // the queued clip c was dropped by the urgent one
});

test("a clip that never starts is skipped after STALL_MS and the next one plays", () => {
  const { players: [p0, p1], createPlayer } = fakePair();
  const timers = [];
  const v = createVoicePlayer({
    createPlayer, getWsUrl: () => "wss://h/ws/phone",
    setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimer: () => {},
  });
  v.playPath("/api/agent/clip/a");
  v.playPath("/api/agent/clip/b");
  assert.equal(timers[0].ms, STALL_MS);
  timers[0].fn(); // a never started
  assert.equal(p1.calls.at(-1)[0], "play"); // b takes over
  assert.equal(p0.calls.filter((c) => c[0] === "play").length, 1);
});
