import test from "node:test";
import assert from "node:assert/strict";
import { normalizeServerUrl } from "../../src/address.js";

const cases = [
  ["https://quiet.trycloudflare.com", "wss://quiet.trycloudflare.com/ws/phone"],
  ["wss://quiet.trycloudflare.com/", "wss://quiet.trycloudflare.com/ws/phone"],
  ["http://192.168.1.5:8000", "ws://192.168.1.5:8000/ws/phone"],
  ["192.168.1.5:8000", "ws://192.168.1.5:8000/ws/phone"],
  ["ws://192.168.1.5:8000/ws/phone", "ws://192.168.1.5:8000/ws/phone"],
  ["  https://x.trycloudflare.com/ws/phone  ", "wss://x.trycloudflare.com/ws/phone"],
];
for (const [input, want] of cases) {
  test(`normalize ${JSON.stringify(input)}`, () => assert.equal(normalizeServerUrl(input), want));
}
test("empty input returns null", () => {
  assert.equal(normalizeServerUrl(""), null);
  assert.equal(normalizeServerUrl(null), null);
});
