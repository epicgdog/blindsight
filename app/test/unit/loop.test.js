import test from "node:test";
import assert from "node:assert/strict";
import { runCaptureLoop, headingRate, frameIntervalMs, STILL_MS, TURN_FAST_DEG_S } from "../../src/loop.js";

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

test("onFrame reports when each frame's capture started", async () => {
  const frames = []; let running = true; let t = 100;
  await runCaptureLoop({
    isRunning: () => running, now: () => (t += 10),
    capture: async () => ({ bytes: new Uint8Array([1]), w: 1, h: 1 }),
    send: async () => { if (frames.length === 3) running = false; },
    getHeading: () => 0,
    onFrame: (f) => frames.push(f),
  });
  assert.deepEqual(frames.map((f) => f.seq), [1, 2, 3]);
  assert.ok(frames.every((f) => Number.isFinite(f.captureStartMs)));
});

test("headers carry pitch and roll when an attitude is available", async () => {
  const headers = []; let running = true;
  await runCaptureLoop({
    isRunning: () => running,
    capture: async () => ({ bytes: new Uint8Array([1]), w: 1, h: 1 }),
    send: async (_b, h) => { headers.push(h); if (headers.length === 2) running = false; },
    getHeading: () => 10,
    getAttitude: () => (headers.length === 0 ? { pitch_deg: -5, roll_deg: 1.5 } : null),
  });
  assert.equal(headers[0].pitch_deg, -5);
  assert.equal(headers[0].roll_deg, 1.5);
  assert.ok(!("pitch_deg" in headers[1]), "no attitude, no keys");
});

test("headers carry moving only when the detector has an answer", async () => {
  const headers = []; let running = true;
  const atts = [{ pitch_deg: 0, roll_deg: 0, moving: false }, { pitch_deg: 0, roll_deg: 0 }];
  await runCaptureLoop({
    isRunning: () => running,
    capture: async () => ({ bytes: new Uint8Array([1]), w: 1, h: 1 }),
    send: async (_b, h) => { headers.push(h); if (headers.length === 2) running = false; },
    getHeading: () => 0,
    getAttitude: () => atts[headers.length],
  });
  assert.equal(headers[0].moving, false);
  assert.ok(!("moving" in headers[1]));
});

test("headingRate is degrees per second, 0 without a usable previous sample", () => {
  assert.equal(headingRate(null, 90, 1000), 0);
  assert.equal(headingRate({ heading: null, t: 0 }, 90, 1000), 0);
  assert.equal(headingRate({ heading: 10, t: 0 }, null, 1000), 0);
  assert.equal(headingRate({ heading: 10, t: 1000 }, 50, 1000), 0); // no time passed
  assert.equal(headingRate({ heading: 10, t: 2000 }, 50, 1000), 0); // clock went backwards
  assert.equal(headingRate({ heading: 0, t: 0 }, 90, 1000), 90);
  assert.equal(headingRate({ heading: 90, t: 0 }, 0, 500), 180);
});

test("headingRate takes the short way round north", () => {
  assert.equal(headingRate({ heading: 350, t: 0 }, 10, 1000), 20);
  assert.equal(headingRate({ heading: 10, t: 0 }, 350, 1000), 20);
});

test("frameIntervalMs slows down only when the phone is known to be still", () => {
  assert.equal(frameIntervalMs({ moving: false }), STILL_MS);
  assert.equal(frameIntervalMs({ moving: true }), 0);
  assert.equal(frameIntervalMs({ moving: undefined }), 0); // unknown motion counts as moving
  assert.equal(frameIntervalMs({ moving: null }), 0);
});

test("frameIntervalMs goes full speed when turning fast, even held still", () => {
  assert.equal(frameIntervalMs({ moving: false, turnDegPerS: TURN_FAST_DEG_S }), 0);
  assert.equal(frameIntervalMs({ moving: false, turnDegPerS: TURN_FAST_DEG_S - 1 }), STILL_MS);
});

test("onRate reports the interval once per change, and a still phone sends frames further apart", async () => {
  const rates = [], stamps = []; let running = true;
  const atts = [{ moving: true }, { moving: true }, { moving: false }, { moving: false }];
  await runCaptureLoop({
    isRunning: () => running,
    capture: async () => ({ bytes: new Uint8Array([1]), w: 1, h: 1 }),
    send: async () => { stamps.push(Date.now()); if (stamps.length === 4) running = false; },
    getHeading: () => 0,
    getAttitude: () => atts[stamps.length],
    onRate: (r) => rates.push(r),
  });
  assert.deepEqual(rates.map((r) => r.interval_ms), [0, STILL_MS]); // two changes, not four calls
  assert.equal(rates[0].moving, true);
  assert.equal(rates[1].moving, false);
  assert.ok(stamps[3] - stamps[2] >= STILL_MS - 50, "a held-still phone waits between frames");
  assert.ok(stamps[1] - stamps[0] < STILL_MS / 2, "a moving phone does not");
});

test("headers carry steps only when it is a non-negative integer", async () => {
  const headers = []; let running = true;
  const steps = [12, -1, 3.5, null];
  await runCaptureLoop({
    isRunning: () => running,
    capture: async () => ({ bytes: new Uint8Array([1]), w: 1, h: 1 }),
    send: async (_b, h) => { headers.push(h); if (headers.length === 4) running = false; },
    getHeading: () => 0,
    getSteps: () => steps[headers.length],
  });
  assert.equal(headers[0].steps, 12);
  for (const h of headers.slice(1)) assert.ok(!("steps" in h));
});
