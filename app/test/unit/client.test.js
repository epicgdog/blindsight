import test from "node:test";
import assert from "node:assert/strict";
import { createClient } from "../../src/client.js";

class FakeWS {
  static instances = [];
  constructor(url) { this.url = url; this.sent = []; FakeWS.instances.push(this); }
  send(d) { this.sent.push(d); }
  close() { this.onclose?.({}); }
  open() { this.readyState = 1; this.onopen?.({}); }
  msg(o) { this.onmessage?.({ data: JSON.stringify(o) }); }
  drop() { this.readyState = 3; this.onclose?.({}); }
}

function setup() {
  FakeWS.instances = [];
  const spoken = [], statuses = [], timers = [];
  const speaker = { handle: (c) => spoken.push(["cue", c?.text]), announce: (t) => spoken.push(["announce", t]) };
  const client = createClient({
    url: "wss://x/ws/phone", WebSocketImpl: FakeWS, speaker,
    onStatus: (s) => statuses.push(s), onResult: () => {},
    setTimeoutImpl: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeoutImpl: () => {},
  });
  return { client, spoken, statuses, timers };
}

test("connect -> status connected", () => {
  const { client, statuses } = setup(); client.start();
  FakeWS.instances[0].open();
  assert.equal(statuses.at(-1).connected, true);
});

test("results route cue to speaker and state to status", () => {
  const { client, spoken, statuses } = setup(); client.start();
  const ws = FakeWS.instances[0]; ws.open();
  ws.msg({ seq: 1, location: { state: "LOCKED" }, cue: { id: "1", text: "You are in the main lobby", priority: 1, interrupt: true } });
  assert.deepEqual(spoken.at(-1), ["cue", "You are in the main lobby"]);
  assert.equal(statuses.at(-1).state, "LOCKED");
});

test("disconnect announces 'Connection lost' once and schedules backed-off reconnect", () => {
  const { client, spoken, timers } = setup(); client.start();
  FakeWS.instances[0].open(); FakeWS.instances[0].drop();
  assert.deepEqual(spoken.filter((s) => s[1] === "Connection lost").length, 1);
  assert.equal(timers.length, 1);
  const first = timers[0].ms;
  timers[0].fn();                    // reconnect attempt fails
  FakeWS.instances[1].drop();
  assert.equal(spoken.filter((s) => s[1] === "Connection lost").length, 1, "no repeated announce while down");
  assert.ok(timers[1].ms > first, "backoff grows");
});

test("reconnect success announces and resets backoff", () => {
  const { client, spoken, timers } = setup(); client.start();
  FakeWS.instances[0].open(); FakeWS.instances[0].drop();
  timers[0].fn(); FakeWS.instances[1].open();
  assert.ok(spoken.some((s) => s[0] === "announce" && /reconnect/i.test(s[1])));
  FakeWS.instances[1].drop();
  assert.equal(timers.at(-1).ms, timers[0].ms, "backoff reset after success");
});

test("stop() prevents reconnect", () => {
  const { client, timers } = setup(); client.start();
  FakeWS.instances[0].open(); client.stop();
  assert.equal(timers.length, 0);
});

test("sendFrame only sends when open and returns false otherwise", async () => {
  const { client } = setup(); client.start();
  assert.equal(await client.sendFrame(new Uint8Array([1])), false);
  FakeWS.instances[0].open();
  assert.equal(await client.sendFrame(new Uint8Array([1])), true);
});
