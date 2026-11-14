import { test } from "node:test";
import assert from "node:assert/strict";
import { MIN_CLIP_MS, RECORD_MODE, agentTalkUrl, postClip } from "../../src/talk.js";

test("agent url follows the websocket scheme and host", () => {
  assert.equal(agentTalkUrl("wss://box.ts.net/ws/phone"), "https://box.ts.net/api/agent/talk");
  assert.equal(agentTalkUrl("ws://10.0.0.2:8000/ws/phone"), "http://10.0.0.2:8000/api/agent/talk");
  assert.equal(agentTalkUrl(""), null);
});

test("record mode only turns recording on", () => {
  assert.equal(RECORD_MODE.allowsRecording, true);
  assert.equal(RECORD_MODE.playsInSilentMode, true);
  assert.ok(MIN_CLIP_MS > 0);
});

class FakeForm {
  constructor() { this.parts = []; }
  append(k, v) { this.parts.push([k, v]); }
}

test("postClip sends the clip and returns the reply", async () => {
  let sent;
  const fetchFn = async (url, opts) => {
    sent = { url, opts };
    return { ok: true, json: async () => ({ heard: "where am I", reply: "You are at scan point 12." }) };
  };
  const out = await postClip(fetchFn, "https://x/api/agent/talk", "file:///clip.m4a", { FormDataCtor: FakeForm });
  assert.equal(out.reply, "You are at scan point 12.");
  assert.equal(sent.opts.method, "POST");
  assert.equal(sent.opts.body.parts[0][0], "audio");
  assert.equal(sent.opts.body.parts[0][1].uri, "file:///clip.m4a");
});

test("postClip always has something to say on failure", async () => {
  const down = await postClip(async () => { throw new Error("offline"); }, "u", "f", { FormDataCtor: FakeForm });
  assert.match(down.reply, /can't reach/);
  const bad = await postClip(async () => ({ ok: false, status: 503 }), "u", "f", { FormDataCtor: FakeForm });
  assert.match(bad.reply, /problem/);
});
