import test from "node:test";
import assert from "node:assert/strict";
import { encodeFrame, decodeFrame } from "../../src/protocol.js";

test("frame layout: u32 BE header length, JSON header, JPEG tail", () => {
  const jpeg = new Uint8Array([0xff, 0xd8, 1, 2, 3, 0xff, 0xd9]);
  const header = { seq: 1, ts_ms: 123, heading_deg: null, w: 240, h: 320 };
  const buf = encodeFrame(header, jpeg);
  const n = new DataView(buf.buffer, buf.byteOffset).getUint32(0, false);
  const json = new TextDecoder().decode(buf.slice(4, 4 + n));
  assert.deepEqual(JSON.parse(json), header);
  assert.deepEqual([...buf.slice(4 + n)], [...jpeg]);
});

test("roundtrip with utf8 and large jpeg", () => {
  const jpeg = new Uint8Array(200000).fill(7);
  const header = { seq: 2, ts_ms: 1, heading_deg: 359.9, w: 1, h: 1 };
  const out = decodeFrame(encodeFrame(header, jpeg));
  assert.deepEqual(out.header, header);
  assert.equal(out.jpeg.length, 200000);
});

test("rejects headers missing required keys", () => {
  assert.throws(() => encodeFrame({ seq: 1 }, new Uint8Array([1])), /header/i);
});

test("decode rejects truncated frames", () => {
  assert.throws(() => decodeFrame(new Uint8Array([0, 0, 0, 9, 1])), /truncated/i);
});
