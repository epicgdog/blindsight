import test from "node:test";
import assert from "node:assert/strict";
import { createCueSpeaker } from "../../src/speech.js";

function fake() {
  const log = [];
  let speaking = false;
  return {
    log,
    set speaking(v) { speaking = v; },
    api: {
      speak: (t) => { log.push(["speak", t]); speaking = true; },
      stop: () => { log.push(["stop"]); speaking = false; },
      isSpeaking: () => speaking,
    },
  };
}
const cue = (id, text, interrupt = false) => ({ id, text, priority: 1, interrupt });

test("speaks a cue when idle", () => {
  const f = fake(); const s = createCueSpeaker(f.api);
  s.handle(cue("a", "Chair, ahead, close"));
  assert.deepEqual(f.log, [["speak", "Chair, ahead, close"]]);
});

test("dedupes by cue id", () => {
  const f = fake(); const s = createCueSpeaker(f.api);
  s.handle(cue("a", "x")); f.speaking = false; s.handle(cue("a", "x"));
  assert.equal(f.log.filter((e) => e[0] === "speak").length, 1);
});

test("non-interrupt cue while speaking is dropped, not queued", () => {
  const f = fake(); const s = createCueSpeaker(f.api);
  s.handle(cue("a", "one")); s.handle(cue("b", "two"));
  f.speaking = false;
  s.handle(null);
  assert.deepEqual(f.log, [["speak", "one"]]);
});

test("interrupt cue stops then speaks", () => {
  const f = fake(); const s = createCueSpeaker(f.api);
  s.handle(cue("a", "one")); s.handle(cue("b", "STOP", true));
  assert.deepEqual(f.log.slice(1), [["stop"], ["speak", "STOP"]]);
});

test("null cue is a no-op", () => {
  const f = fake(); createCueSpeaker(f.api).handle(null);
  assert.deepEqual(f.log, []);
});

test("announce always interrupts and is not deduped by cue ids", () => {
  const f = fake(); const s = createCueSpeaker(f.api);
  s.handle(cue("a", "one")); s.announce("Connection lost");
  assert.deepEqual(f.log.slice(1), [["stop"], ["speak", "Connection lost"]]);
});

test("startup speaks 'Blindsight ready' and logs that speech played", async () => {
  const f = fake(); const logs = [];
  const s = createCueSpeaker(f.api, { log: (m) => logs.push(m) });
  await s.startupCheck();
  assert.deepEqual(f.log.find((e) => e[0] === "speak"), ["speak", "Blindsight ready"]);
  assert.ok(logs.some((m) => /speech played/i.test(m)));
});

test("seen ids are bounded so memory cannot grow forever", () => {
  const f = fake(); const s = createCueSpeaker(f.api, { maxSeen: 5 });
  for (let i = 0; i < 50; i++) { f.speaking = false; s.handle(cue("id" + i, "t")); }
  assert.ok(s.seenCount() <= 5);
});
