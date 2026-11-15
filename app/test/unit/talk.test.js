import { test } from "node:test";
import assert from "node:assert/strict";
import { MIN_CLIP_MS, RECORD_MODE, agentTalkUrl, postClip, createHoldToTalk } from "../../src/talk.js";
import { normalizeServerUrl } from "../../src/address.js";

test("bare Tailscale address uses HTTPS for voice upload", () => {
  assert.equal(agentTalkUrl(normalizeServerUrl("minky.tailb9ac0d.ts.net")), "https://minky.tailb9ac0d.ts.net/api/agent/talk");
  assert.equal(normalizeServerUrl("192.168.1.5:8000"), "ws://192.168.1.5:8000/ws/phone");
});

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

const readBytes = async () => new Uint8Array([1, 2, 3]);

test("postClip sends the clip bytes raw and returns the reply", async () => {
  let sent;
  const fetchFn = async (url, opts) => {
    sent = { url, opts };
    return { ok: true, json: async () => ({ heard: "where am I", reply: "You are at scan point 12.", url: "/api/agent/clip/x" }) };
  };
  const out = await postClip(fetchFn, "https://x/api/agent/talk", "file:///clip.m4a", { readBytes });
  assert.equal(out.reply, "You are at scan point 12.");
  assert.equal(out.url, "/api/agent/clip/x");
  assert.equal(sent.opts.method, "POST");
  assert.equal(sent.opts.headers["Content-Type"], "audio/m4a");
  assert.deepEqual([...sent.opts.body], [1, 2, 3]);
});

test("postClip always has something to say on failure", async () => {
  const down = await postClip(async () => { throw new Error("offline"); }, "u", "f", { readBytes });
  assert.match(down.reply, /can't reach/);
  const bad = await postClip(async () => ({ ok: false, status: 503 }), "u", "f", { readBytes });
  assert.match(bad.reply, /not ready/);
  const busy = await postClip(async () => ({ ok: false, status: 409 }), "u", "f", { readBytes });
  assert.match(busy.reply, /wait/);
  const broken = await postClip(async () => ({ ok: false, status: 500 }), "u", "f", { readBytes });
  assert.match(broken.reply, /problem/);
  const unread = await postClip(async () => { throw new Error("no"); }, "u", "f", { readBytes: async () => { throw new Error("gone"); } });
  assert.match(unread.reply, /read the recording/);
});

test("timeout is reported separately from network failure", async () => {
  const out = await postClip(async () => { throw {name: "AbortError"}; }, "u", "f", { readBytes });
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

test("onResult receives the whole reply (with its audio url) instead of announcing the text", async () => {
  let clock = 0; const events = [], results = [];
  const controller = createHoldToTalk({
    prepare: async () => {}, record: () => {}, finish: async () => "file", restore: async () => {},
    send: async () => ({ reply: "text", url: "/api/agent/clip/x" }),
    onResult: (r) => results.push(r), announce: (t) => events.push(t), state() {}, busy() {}, now: () => clock,
  });
  controller.begin(); await new Promise((r) => setTimeout(r, 0)); clock = 1000;
  await controller.end();
  assert.deepEqual(results, [{ reply: "text", url: "/api/agent/clip/x" }]);
  assert.deepEqual(events, []); // not also spoken as text
});

test("busy(false) runs after every attempt, so a clip too short to send cannot leave the agent muted", async () => {
  let clock = 0; const busy = [];
  const controller = createHoldToTalk({
    prepare: async () => {}, record: () => {}, finish: async () => "file", restore: async () => {},
    send: async () => { throw new Error("must not upload a tap"); },
    announce() {}, state() {}, busy: (v) => busy.push(v), now: () => clock,
  });
  controller.begin(); await new Promise((r) => setTimeout(r, 0)); clock = 10; // released after 10 ms
  await controller.end();
  assert.deepEqual(busy, [true, false]);
});
