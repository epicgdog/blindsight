// Drives the real app modules (loop + protocol + client + speech) against the echo server.
import test from "node:test";
import assert from "node:assert/strict";
import { createClient } from "../../src/client.js";
import { createCueSpeaker } from "../../src/speech.js";
import { encodeFrame } from "../../src/protocol.js";
import { runCaptureLoop } from "../../src/loop.js";

const WS = process.env.BLINDSIGHT_WS;
const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 16, 74, 70, 73, 70, 0, 1, 1, 0, 0, 1, 0, 1, 0, 0, 0xff, 0xd9]);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function mkSpeaker() {
  const said = []; let speaking = false;
  const speaker = createCueSpeaker({
    speak: (t) => { said.push(t); speaking = true; setTimeout(() => (speaking = false), 20); },
    stop: () => { speaking = false; },
    isSpeaking: () => speaking,
  });
  return { speaker, said };
}

test("streams frames, gets results under 300 ms, speaks location cue", { skip: !WS }, async () => {
  const { speaker, said } = mkSpeaker();
  const results = []; const sendTimes = new Map();
  const client = createClient({
    url: `${WS}/ws/phone`, WebSocketImpl: globalThis.WebSocket, speaker,
    onStatus: () => {}, onResult: (r) => results.push([r, Date.now()]),
  });
  client.start();
  for (let i = 0; i < 50 && !client.isOpen(); i++) await sleep(50);
  assert.ok(client.isOpen(), "connected to echo server");

  let running = true;
  const loop = runCaptureLoop({
    isRunning: () => running,
    capture: async () => ({ bytes: JPEG, w: 240, h: 320 }),
    send: async (bytes, header) => { sendTimes.set(header.seq, Date.now()); await client.sendFrame(encodeFrame(header, bytes)); },
    getHeading: () => 45,
  });
  const deadline = Date.now() + 5000;
  while (results.length < 10 && Date.now() < deadline) await sleep(20);
  running = false; await loop; client.stop();

  assert.ok(results.length >= 10, `got ${results.length} results`);
  const rtts = results.map(([r, t]) => t - sendTimes.get(r.seq)).filter(Number.isFinite).sort((a, b) => a - b);
  assert.ok(rtts[Math.floor(rtts.length / 2)] < 300, `median rtt ${rtts}`);
  assert.ok(said.some((t) => /main lobby/i.test(t)), `spoke: ${said}`);
  assert.equal(said.filter((t) => /main lobby/i.test(t)).length, 1, "location cue deduped across repeated results");
});
