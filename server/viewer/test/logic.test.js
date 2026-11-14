import test from "node:test";
import assert from "node:assert/strict";
import { stateClass, pushHistory, sparklinePoints, boxStyle, appendCue, fpsFromTimes, refStrip } from "../logic.js";

test("stateClass maps states and defaults to lost", () => {
  assert.equal(stateClass("LOCKED"), "state-locked");
  assert.equal(stateClass("UNCERTAIN"), "state-uncertain");
  assert.equal(stateClass("LOST"), "state-lost");
  assert.equal(stateClass("weird"), "state-lost");
});

test("history is bounded and keeps newest", () => {
  let h = []; for (let i = 0; i < 100; i++) h = pushHistory(h, i, 60);
  assert.equal(h.length, 60); assert.equal(h.at(-1), 99); assert.equal(h[0], 40);
});

test("sparkline points span the box and survive flat or tiny input", () => {
  assert.equal(sparklinePoints([], 100, 20), "");
  const p = sparklinePoints([0, 1], 100, 20).split(" ");
  assert.deepEqual(p, ["0,20", "100,0"]);
  assert.ok(!sparklinePoints([0.5, 0.5, 0.5], 100, 20).includes("NaN"));
});

test("box style converts normalized bbox to percentages", () => {
  assert.deepEqual(boxStyle([0.1, 0.2, 0.5, 0.6]), { left: "10%", top: "20%", width: "40%", height: "40%" });
});

test("cue log dedupes by id, timestamps, and bounds length", () => {
  let log = [];
  log = appendCue(log, { id: "1", text: "A" }, 0, 3);
  log = appendCue(log, { id: "1", text: "A" }, 1000, 3);
  assert.equal(log.length, 1);
  for (let i = 2; i < 10; i++) log = appendCue(log, { id: String(i), text: "t" + i }, i, 3);
  assert.equal(log.length, 3);
  assert.equal(appendCue([], null, 0, 3).length, 0);
});

test("fps from rolling timestamps", () => {
  assert.equal(fpsFromTimes([]), 0);
  assert.equal(Math.round(fpsFromTimes([0, 100, 200, 300, 400])), 10);
});

test("ref strip highlights only best_ref", () => {
  const out = refStrip(["a", "b", "c"], "b");
  assert.deepEqual(out.map((r) => r.highlight), [false, true, false]);
  assert.deepEqual(refStrip(["a"], null).map((r) => r.highlight), [false]);
});
