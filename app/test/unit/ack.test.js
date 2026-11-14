import test from "node:test";
import assert from "node:assert/strict";
import { speechAckUrl, ackBody, sendAck, createAckTracker } from "../../src/ack.js";

test("speech ack url is derived from the phone websocket url", () => {
  assert.equal(speechAckUrl("wss://x.ts.net/ws/phone"), "https://x.ts.net/api/speech");
  assert.equal(speechAckUrl("ws://10.0.0.5:8000/ws/phone"), "http://10.0.0.5:8000/api/speech");
  assert.equal(speechAckUrl(null), null);
  assert.equal(speechAckUrl("not a url"), null);
});

test("ack body carries the cue id and a rounded, non-negative latency", () => {
  assert.deepEqual(ackBody("c1", 431.6), { played: true, cue_id: "c1", capture_to_audible_ms: 432 });
  assert.deepEqual(ackBody("c1", -5), { played: true, cue_id: "c1", capture_to_audible_ms: 0 });
});

test("startup ack has no cue id and no latency", () => {
  assert.deepEqual(ackBody(null, null), { played: true });
  assert.deepEqual(ackBody("c1", NaN), { played: true, cue_id: "c1" });
});

test("sendAck posts JSON and never throws", async () => {
  const calls = [];
  const ok = await sendAck(async (url, init) => { calls.push([url, init]); return { ok: true }; }, "https://h/api/speech", { played: true });
  assert.equal(ok, true);
  assert.equal(calls[0][0], "https://h/api/speech");
  assert.equal(calls[0][1].method, "POST");
  assert.deepEqual(JSON.parse(calls[0][1].body), { played: true });
  assert.equal(await sendAck(async () => { throw new Error("offline"); }, "https://h/api/speech", {}), false);
  assert.equal(await sendAck(async () => ({ ok: false }), "https://h/api/speech", {}), false);
  assert.equal(await sendAck(async () => ({ ok: true }), null, {}), false);
});

test("tracker measures capture to speech start from the frame that produced the cue", () => {
  const t = createAckTracker();
  t.noteFrame(7, 1000);          // capture of frame 7 started at t=1000
  t.noteCue("cue-a", 7);
  assert.equal(t.captureToAudibleMs("cue-a", 1431), 431);
  assert.equal(t.captureToAudibleMs("cue-a", 1500), null, "each cue is measured once");
  assert.equal(t.captureToAudibleMs("unknown", 1500), null);
});

test("tracker memory stays bounded", () => {
  const t = createAckTracker(5);
  for (let i = 0; i < 50; i++) t.noteFrame(i, i);
  t.noteCue("old", 0);
  assert.equal(t.captureToAudibleMs("old", 100), null, "frame 0 was forgotten");
  assert.ok(t.size() <= 5);
});
