import test from "node:test";
import assert from "node:assert/strict";
import { isUprightPortrait, smoothGravity, tiltDegrees } from "../../src/orientation.js";

// iOS CoreMotion: a phone held upright in portrait reads y = -1 (units of g).
const g = (x, y, z) => ({ x, y, z });

test("upright portrait is accepted", () => {
  assert.equal(isUprightPortrait(g(0, -1, 0)), true);
  assert.equal(isUprightPortrait(g(0.02, -0.99, -0.05)), true);
});

test("a small tilt up or down and a small roll are still accepted", () => {
  const tilt = (deg) => g(0, -Math.cos((deg * Math.PI) / 180), -Math.sin((deg * Math.PI) / 180));
  assert.equal(isUprightPortrait(tilt(20)), true);
  assert.equal(isUprightPortrait(tilt(-20)), true);
  assert.equal(isUprightPortrait(g(Math.sin(0.3), -Math.cos(0.3), 0)), true); // ~17 deg roll
});

test("a large tilt is rejected (phone pointed at the floor or ceiling)", () => {
  assert.equal(isUprightPortrait(g(0, -0.5, -0.87)), false); // 60 deg
  assert.equal(isUprightPortrait(g(0, -0.1, -0.99)), false); // nearly flat
  assert.equal(isUprightPortrait(g(0, 0, -1)), false); // lying face up/down
});

test("landscape is rejected", () => {
  assert.equal(isUprightPortrait(g(-1, 0, 0)), false);
  assert.equal(isUprightPortrait(g(1, 0, 0)), false);
});

test("upside-down portrait is rejected", () => {
  assert.equal(isUprightPortrait(g(0, 1, 0)), false);
});

test("the tilt limit is configurable", () => {
  const tilt45 = g(0, -Math.SQRT1_2, -Math.SQRT1_2);
  assert.equal(isUprightPortrait(tilt45, { maxTiltDeg: 30 }), false);
  assert.equal(isUprightPortrait(tilt45, { maxTiltDeg: 50 }), true);
});

test("missing or zero readings are not upright", () => {
  assert.equal(isUprightPortrait(null), false);
  assert.equal(isUprightPortrait(g(0, 0, 0)), false);
  assert.equal(isUprightPortrait(g(NaN, -1, 0)), false);
});

test("tiltDegrees measures the angle from vertical-portrait", () => {
  assert.ok(Math.abs(tiltDegrees(g(0, -1, 0))) < 1e-9);
  assert.ok(Math.abs(tiltDegrees(g(-1, 0, 0)) - 90) < 1e-6);
});

test("smoothing blends toward new readings and starts from the first", () => {
  assert.deepEqual(smoothGravity(null, g(0, -1, 0), 0.5), g(0, -1, 0));
  const s = smoothGravity(g(0, -1, 0), g(0, 0, 0), 0.25);
  assert.equal(s.y, -0.75);
});
