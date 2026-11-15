import { test } from "node:test";
import assert from "node:assert/strict";
import { MIN_CLIP_MS, RECORD_MODE, agentTalkUrl, postClip, createHoldToTalk } from "../../src/talk.js";

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
  assert.match(bad.reply, /not ready/);
});

test("timeout is reported separately from network failure", async () => {
  const out = await postClip(async () => { throw {name: "AbortError"}; }, "u", "f", {FormDataCtor: FakeForm});
  assert.match(out.reply, /took too long/);
});

test("release waits for recorder preparation and never stops an unstarted recorder", async () => {
  let ready, clock = 0; const events = [];
  const controller = createHoldToTalk({
    prepare: () => new Promise(r => ready = r), record: () => events.push("record"),
    finish: async () => { events.push("stop"); return "file"; }, restore: async () => {},
    send: async () => { events.push("upload"); return {reply:"answer"}; },
    announce: text => events.push(text), state() {}, busy() {}, now: () => clock,
  });
  controller.begin(); const end = controller.end();
  assert.deepEqual(events, []); ready(); await end;
  assert.deepEqual(events, ["record", "stop"]); // early release is a short clip, not corrupt audio
});

test("one recording and upload at a time; restore playback before response", async () => {
  let clock = 0, upload; const events = [];
  const controller = createHoldToTalk({prepare: async()=>{}, record:()=>events.push("record"),
    finish:async()=>"file", restore:async()=>events.push("restore"),
    send:()=>new Promise(r=>upload=r), announce:()=>events.push("reply"), state(){},busy(){},now:()=>clock});
  controller.begin(); await new Promise(r=>setTimeout(r,0)); clock=1000;
  const end=controller.end(); await new Promise(r=>setTimeout(r,0)); controller.begin();
  assert.equal(events.filter(x=>x==="record").length,1);
  upload({reply:"answer"}); await end;
  assert.ok(events.indexOf("restore") < events.indexOf("reply"));
});
