import test from "node:test";
import assert from "node:assert/strict";
import { imuMessage } from "../../src/imu.js";

test("a full sample carries every field", () => {
  const m = imuMessage({ now: 1234, heading: 90, attitude: { pitch_deg: -5, roll_deg: 2, moving: true }, steps: 17 });
  assert.deepEqual(m, { type: "imu", t_ms: 1234, heading_deg: 90, pitch_deg: -5, roll_deg: 2, moving: true, steps: 17 });
});

test("unknown values are null, never undefined or garbage", () => {
  const m = imuMessage({});
  assert.deepEqual(m, { type: "imu", t_ms: null, heading_deg: null, pitch_deg: null, roll_deg: null, moving: null, steps: null });
  assert.deepEqual(JSON.parse(JSON.stringify(m)), m); // survives the wire unchanged
});

test("heading is only sent inside [0, 360)", () => {
  for (const h of [-1, 360, 400, NaN, Infinity, "90", null, undefined]) {
    assert.equal(imuMessage({ heading: h }).heading_deg, null, String(h));
  }
  assert.equal(imuMessage({ heading: 0 }).heading_deg, 0);
  assert.equal(imuMessage({ heading: 359.9 }).heading_deg, 359.9);
});

test("only a real boolean counts as moving", () => {
  assert.equal(imuMessage({ attitude: { moving: false } }).moving, false);
  for (const v of [1, 0, "true", null, undefined]) assert.equal(imuMessage({ attitude: { moving: v } }).moving, null, String(v));
});

test("steps must be an integer; non-finite pitch and roll are dropped", () => {
  assert.equal(imuMessage({ steps: 0 }).steps, 0);
  for (const s of [1.5, NaN, "3", null, undefined]) assert.equal(imuMessage({ steps: s }).steps, null, String(s));
  const m = imuMessage({ attitude: { pitch_deg: NaN, roll_deg: "1" } });
  assert.equal(m.pitch_deg, null);
  assert.equal(m.roll_deg, null);
});

test("a null attitude or missing time is fine", () => {
  assert.equal(imuMessage({ attitude: null, now: "x" }).t_ms, null);
});
