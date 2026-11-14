import test from "node:test";
import assert from "node:assert/strict";
import { base64ToBytes } from "../../src/base64.js";

test("decodes base64 to bytes, with padding and data-url prefix", () => {
  assert.deepEqual([...base64ToBytes("/9j/")], [0xff, 0xd8, 0xff]);
  assert.deepEqual([...base64ToBytes("AQI=")], [1, 2]);
  assert.deepEqual([...base64ToBytes("data:image/jpeg;base64,AQID")], [1, 2, 3]);
});

test("matches Buffer for random data", () => {
  const b = Buffer.from(Array.from({ length: 1000 }, (_, i) => (i * 37) % 256));
  assert.deepEqual([...base64ToBytes(b.toString("base64"))], [...b]);
});
