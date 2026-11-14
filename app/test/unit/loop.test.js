import test from "node:test";
import assert from "node:assert/strict";
import { runCaptureLoop } from "../../src/loop.js";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

test("sequential: never captures before the previous send returns", async () => {
  let inFlight = 0, maxInFlight = 0, sent = [];
  let running = true;
  await runCaptureLoop({
    isRunning: () => running,
    capture: async () => { inFlight++; maxInFlight = Math.max(maxInFlight, inFlight); await sleep(5); return { bytes: new Uint8Array([1]), w: 10, h: 20 }; },
    send: async (buf) => { await sleep(10); inFlight--; sent.push(buf); if (sent.length === 5) running = false; },
    getHeading: () => 90,
  });
  assert.equal(maxInFlight, 1);
  assert.equal(sent.length, 5);
});

test("seq increments, headers carry heading, w and h, ts_ms from clock", async () => {
  const headers = []; let running = true; let t = 1000;
  await runCaptureLoop({
    isRunning: () => running, now: () => (t += 7),
    capture: async () => ({ bytes: new Uint8Array([9]), w: 240, h: 320 }),
    send: async (_buf, header) => { headers.push(header); if (headers.length === 3) running = false; },
    getHeading: () => null,
  });
  assert.deepEqual(headers.map((h) => h.seq), [1, 2, 3]);
  assert.ok(headers.every((h) => h.heading_deg === null && h.w === 240 && h.h === 320 && h.ts_ms > 1000));
});

test("capture timing is reported and warns over 250 ms", async () => {
  const timings = []; let running = true; let t = 0;
  await runCaptureLoop({
    isRunning: () => running, now: () => t,
    capture: async () => { t += 300; return { bytes: new Uint8Array([1]), w: 1, h: 1 }; },
    send: async () => { running = false; },
    getHeading: () => 0,
    onTiming: (x) => timings.push(x),
  });
  assert.equal(timings[0].captureMs, 300);
  assert.equal(timings[0].slow, true);
});

test("a capture or send error does not kill the loop", async () => {
  let running = true, calls = 0, sent = 0;
  await runCaptureLoop({
    isRunning: () => running, retryDelayMs: 1,
    capture: async () => { if (++calls === 1) throw new Error("camera hiccup"); return { bytes: new Uint8Array([1]), w: 1, h: 1 }; },
    send: async () => { if (++sent === 2) running = false; },
    getHeading: () => 0,
  });
  assert.equal(sent, 2);
});

test("capture is skipped while canCapture is false, and resumes without losing the sequence", async () => {
  let running = true, allowed = false, captures = 0, blocked = 0;
  const sent = [];
  setTimeout(() => { allowed = true; }, 40);
  await runCaptureLoop({
    isRunning: () => running,
    canCapture: () => allowed,
    onBlocked: () => { blocked += 1; },
    blockedPollMs: 5,
    capture: async () => { captures += 1; return { bytes: new Uint8Array([1]), w: 10, h: 20 }; },
    send: async (_b, header) => { sent.push(header.seq); if (sent.length === 3) running = false; },
    getHeading: () => 0,
  });
  assert.ok(blocked >= 3, `blocked ${blocked} times while tilted`);
  assert.equal(captures, 3, "no capture happened while blocked");
  assert.deepEqual(sent, [1, 2, 3]);
});

test("stopping while blocked ends the loop", async () => {
  let running = true;
  setTimeout(() => { running = false; }, 30);
  await runCaptureLoop({
    isRunning: () => running, canCapture: () => false, blockedPollMs: 5,
    capture: async () => { throw new Error("must not capture"); },
    send: async () => {}, getHeading: () => 0,
  });
});
