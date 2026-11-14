import test from "node:test";
import assert from "node:assert/strict";
import { attitudeFromGravity, createMotionDetector } from "../../src/attitude.js";

// iOS CoreMotion gravity in device axes (x right, y toward the top edge, z out of the screen), in g.
const near = (a, b, tol = 0.5) => Math.abs(a - b) <= tol;

test("upright portrait, camera level: pitch 0, roll 0", () => {
  const a = attitudeFromGravity({ x: 0, y: -1, z: 0 });
  assert.ok(near(a.pitch_deg, 0) && near(a.roll_deg, 0), JSON.stringify(a));
});

test("camera tilted up 30 degrees gives pitch +30", () => {
  const t = (30 * Math.PI) / 180;
  const a = attitudeFromGravity({ x: 0, y: -Math.cos(t), z: Math.sin(t) });
  assert.ok(near(a.pitch_deg, 30), JSON.stringify(a));
});

test("camera pointed at the floor (screen facing up) gives pitch -90", () => {
  assert.ok(near(attitudeFromGravity({ x: 0, y: 0, z: -1 }).pitch_deg, -90));
});

test("camera pointed at the ceiling gives pitch +90", () => {
  assert.ok(near(attitudeFromGravity({ x: 0, y: 0, z: 1 }).pitch_deg, 90));
});

test("landscape is roll of about 90 degrees, pitch unaffected", () => {
  const a = attitudeFromGravity({ x: -1, y: 0, z: 0 });
  assert.ok(near(Math.abs(a.roll_deg), 90) && near(a.pitch_deg, 0), JSON.stringify(a));
});

test("values are rounded and stay inside the wire-format ranges", () => {
  const a = attitudeFromGravity({ x: 0.123, y: -0.98, z: 0.05 });
  assert.ok(a.pitch_deg >= -90 && a.pitch_deg <= 90 && a.roll_deg >= -180 && a.roll_deg <= 180);
  assert.equal(a.pitch_deg, Math.round(a.pitch_deg * 10) / 10);
});

test("missing or zero readings give no attitude", () => {
  assert.equal(attitudeFromGravity(null), null);
  assert.equal(attitudeFromGravity({ x: 0, y: 0, z: 0 }), null);
  assert.equal(attitudeFromGravity({ x: NaN, y: -1, z: 0 }), null);
});

test("motion detector: standing still, then walking, then still again", () => {
  const d = createMotionDetector();
  let t = 0, out = null;
  const feed = (n, f) => { for (let i = 0; i < n; i++, t += 100) out = d.push(f(i), t); return out; };
  const still = (i) => ({ x: 0.003 * Math.sin(i), y: -1 + 0.004 * Math.cos(i * 1.7), z: 0.002 });
  // steps at ~2 Hz: magnitude bounces about +/-0.2 g
  const walk = (i) => ({ x: 0.05 * Math.sin(i), y: -1 - 0.2 * Math.sin((i * Math.PI) / 2.5), z: 0.05 });
  assert.equal(feed(4, still), null, "not enough samples yet");
  assert.equal(feed(20, still), false);
  assert.equal(feed(20, walk), true);
  assert.equal(feed(30, still), false);
});

test("motion detector: turning in place (gravity direction changes slowly) stays still", () => {
  const d = createMotionDetector();
  let out = null;
  for (let i = 0; i < 40; i++) {
    const a = (i / 40) * 0.5; // tilt sweep; magnitude stays 1 g
    out = d.push({ x: Math.sin(a) * 0.2, y: -Math.sqrt(1 - (Math.sin(a) * 0.2) ** 2), z: 0 }, i * 100);
  }
  assert.equal(out, false);
});
