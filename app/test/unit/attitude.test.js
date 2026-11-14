import test from "node:test";
import assert from "node:assert/strict";
import { attitudeFromGravity } from "../../src/attitude.js";

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
