import test from "node:test";
import assert from "node:assert/strict";
import { format, makeLog, shortUri } from "../../src/log.js";

test("log lines carry time, tag, message and data", () => {
  assert.match(format("voice", "playing", { clip: "agent/clip/ab" }), /^\d\d:\d\d:\d\d\.\d{3} \[voice\] playing \{"clip":"agent\/clip\/ab"\}$/);
  const lines = [];
  makeLog("ws", (l) => lines.push(l))("connected");
  assert.match(lines[0], /\[ws\] connected$/);
});

test("clip and tts urls are shortened for the log", () => {
  assert.equal(shortUri("https://h/api/agent/clip/ab12"), "agent/clip/ab12");
  assert.equal(shortUri("https://h/api/voice?text=Blindsight%20ready"), 'tts "Blindsight ready"');
});
