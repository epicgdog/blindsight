import test from "node:test";
import assert from "node:assert/strict";
import { backoffMs } from "../../src/reconnect.js";

test("backoff grows exponentially and is capped", () => {
  const d = [0, 1, 2, 3, 4, 10, 100].map((a) => backoffMs(a, { jitter: false }));
  assert.deepEqual(d.slice(0, 4), [500, 1000, 2000, 4000]);
  assert.equal(d[6], 8000);
  assert.ok(d.every((x, i) => i === 0 || x >= d[i - 1]));
});

test("jitter stays within +/-25 percent", () => {
  for (let i = 0; i < 200; i++) {
    const v = backoffMs(2, { jitter: true, rand: Math.random });
    assert.ok(v >= 1500 && v <= 2500, String(v));
  }
});
