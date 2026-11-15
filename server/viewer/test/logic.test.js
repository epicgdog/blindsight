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

test("ref strip matches face names like face_2", () => {
  const urls = ["/pano/face_0.jpg", "/pano/face_1.jpg", "/pano/face_2.jpg"];
  assert.deepEqual(refStrip(urls, "face_2").map((r) => r.highlight), [false, false, true]);
  assert.deepEqual(refStrip(urls, "/pano/face_1.jpg").map((r) => r.highlight), [false, true, false]);
  assert.deepEqual(refStrip(urls, 0).map((r) => r.highlight), [true, false, false]);
});

import { scene3d, CAM_HEIGHT_M, ENTRANCE_ID } from "../logic.js";

test("scene3d keeps real heights, marks the active and estimate points, and places the floor", () => {
  const map = {
    points: [
      { id: "a", x: 0, y: 0, z: 1.5, floor: 0, room: "Hallway" },
      { id: "b", x: 4, y: 2, z: 1.5, floor: 0 },
      { id: "c", x: 1, y: 1, z: 5.2, floor: 1 },
    ],
    floors: [{ floor: 0, z: 1.5, count: 2 }, { floor: 1, z: 5.2, count: 1 }],
    walls: [[0, 0, 4, 0, 99]],
  };
  const s = scene3d(map, "a", [{ place: "b" }], ["b"]);
  assert.equal(s.points.find((p) => p.id === "a").kind, "active");
  assert.equal(s.points.find((p) => p.id === "c").z, 5.2);
  assert.equal(s.ground_z, +(1.5 - CAM_HEIGHT_M).toFixed(2));
  assert.deepEqual(s.walls, [[0, 0, 4, 0]]);
  assert.deepEqual(s.center, { x: 2, y: 1, z: +(1.5 - CAM_HEIGHT_M + 1).toFixed(2) });
  assert.deepEqual(s.focus, { id: "a", name: "Hallway", floor: 0, otherFloor: false });
  const est = scene3d(map, null, [{ place: "c" }], []);
  assert.equal(est.points.find((p) => p.id === "c").kind, "estimate");
  assert.equal(est.focus.otherFloor, true);
  assert.equal(scene3d({ points: [] }, null, [], []), null);
});

test("scene3d labels the entrance scan point and only that one", () => {
  const map = {
    points: [
      { id: ENTRANCE_ID, x: -10.9, y: -4.1, z: 1.37, floor: 0 },
      { id: "b", x: 4, y: 2, z: 1.5, floor: 0 },
    ],
    floors: [{ floor: 0, z: 1.5, count: 2 }],
    walls: [],
  };
  const s = scene3d(map, null, [], []);
  assert.equal(s.points.find((p) => p.id === ENTRANCE_ID).label, "Entrance");
  assert.equal(s.points.find((p) => p.id === "b").label, undefined);
});
