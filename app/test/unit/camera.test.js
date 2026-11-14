import test from "node:test";
import assert from "node:assert/strict";
import { PICTURE_SIZE, PICTURE_OPTIONS, megapixels } from "../../src/camera.js";

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
  assert.equal(PICTURE_OPTIONS.base64, true);
  assert.equal(PICTURE_OPTIONS.exif, false);
  assert.equal(PICTURE_OPTIONS.skipProcessing, false);
});
