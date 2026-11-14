import test from "node:test";
import assert from "node:assert/strict";
import { PICTURE_SIZE, PICTURE_OPTIONS, megapixels, resizePlan, TARGET_SHORT_SIDE, SAVE_OPTIONS } from "../../src/camera.js";

const SERVER_MAX_PIXELS = 12_000_000; // server/config.py max_image_pixels

test("capture size is 720p, a preset the iOS camera supports", () => {
  assert.equal(PICTURE_SIZE, "1280x720");
});

test("720p frame is far below the server's pixel limit", () => {
  const [w, h] = PICTURE_SIZE.split("x").map(Number);
  assert.ok(w * h < SERVER_MAX_PIXELS / 4);
  assert.equal(megapixels(w, h), 0.92);
});

test("a full iPhone photo would exceed the limit (the bug this prevents)", () => {
  assert.ok(4032 * 3024 > SERVER_MAX_PIXELS);
});

test("picture options keep the loop fast and the payload bytes available", () => {
  assert.equal(PICTURE_OPTIONS.base64, false); // bytes come from the resize step instead
  assert.equal(PICTURE_OPTIONS.exif, false);
  assert.equal(PICTURE_OPTIONS.skipProcessing, false);
});

test("target is 720 pixels on the short side", () => {
  assert.equal(TARGET_SHORT_SIDE, 720);
});

test("a 12 MP landscape iPhone photo resizes to 720 high (960x720)", () => {
  assert.deepEqual(resizePlan(4032, 3024), { height: 720 });
});

test("a 12 MP portrait photo resizes to 720 wide (720x960)", () => {
  assert.deepEqual(resizePlan(3024, 4032), { width: 720 });
});

test("frames already near 720 are left alone, and never upscaled", () => {
  assert.equal(resizePlan(1280, 720), null);
  assert.equal(resizePlan(960, 720), null);
  assert.equal(resizePlan(720, 1280), null);
  assert.equal(resizePlan(640, 480), null);
});

test("bad dimensions give no plan", () => {
  assert.equal(resizePlan(0, 0), null);
  assert.equal(resizePlan(undefined, 100), null);
  assert.equal(resizePlan(NaN, 100), null);
});

test("saved JPEGs are compressed and returned as base64", () => {
  assert.equal(SAVE_OPTIONS.base64, true);
  assert.ok(SAVE_OPTIONS.compress > 0 && SAVE_OPTIONS.compress <= 0.8);
});
