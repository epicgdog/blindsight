import test from "node:test";
import assert from "node:assert/strict";
import { mapMarkers } from "../logic.js";

const map = {
  points: [
    { id: "a", x: 0, y: 0, floor: 0, room: "Hallway", label: "0" },
    { id: "b", x: 10, y: 0, floor: 0, room: null, label: "7" },
    { id: "c", x: 10, y: 5, floor: 0, room: "Stairs", label: "8" },
    { id: "u", x: 1, y: 1, floor: 1, room: "Upper", label: "9" },
  ],
  walls: [[0, 0, 10, 0], [10, 0, 10, 5]],
  bounds: { min_x: 0, max_x: 10, min_y: 0, max_y: 5 },
};

test("with walls, the map frames floor 1 and projects walls and points the same way", () => {
  const m = mapMarkers(map, "a", [], [], 400, 400);
  assert.equal(m.floor, 0);
  assert.equal(m.walls.length, 2);
  assert.ok(m.viewBox.w > 0 && m.viewBox.h > 0);
  const a = m.markers.find((k) => k.id === "a");
  const b = m.markers.find((k) => k.id === "b");
  assert.ok(b.x > a.x, "x grows to the right");
  // the first wall runs from point a to point b, so its ends coincide with their markers
  assert.deepEqual([m.walls[0][0], m.walls[0][1]], [a.x, a.y]);
  assert.deepEqual([m.walls[0][2], m.walls[0][3]], [b.x, b.y]);
});

test("north (larger y) is at the top", () => {
  const m = mapMarkers(map, "a", [], [], 400, 400);
  const b = m.markers.find((k) => k.id === "b");
  const c = m.markers.find((k) => k.id === "c");
  assert.ok(c.y < b.y);
});

test("only floor 1 points are drawn", () => {
  const m = mapMarkers(map, "a", [], [], 400, 400);
  assert.ok(!m.markers.some((k) => k.id === "u"));
});

test("when locked the active place is the estimated position", () => {
  const m = mapMarkers(map, "b", [{ place: "a", sim: 0.5 }], [], 400, 400);
  assert.equal(m.markers.find((k) => k.id === "b").kind, "active");
  assert.equal(m.markers.find((k) => k.id === "a").kind, "candidate");
  assert.equal(m.name, "Scan 7");
});

test("when not locked the top ranked place is shown as the estimate", () => {
  const m = mapMarkers(map, null, [{ place: "c", sim: 0.8 }, { place: "a", sim: 0.5 }], [], 400, 400);
  assert.equal(m.markers.find((k) => k.id === "c").kind, "estimate");
  assert.equal(m.markers.find((k) => k.id === "a").kind, "candidate");
  assert.equal(m.name, "Stairs");
});

test("an estimate on another floor is flagged, not drawn", () => {
  const m = mapMarkers(map, null, [{ place: "u", sim: 0.9 }], [], 400, 400);
  assert.equal(m.otherFloor, true);
  assert.ok(!m.markers.some((k) => k.kind === "estimate" || k.kind === "active"));
  assert.equal(m.name, "Upper");
});

test("without walls the older point-only map still works", () => {
  const m = mapMarkers({ ...map, walls: [] }, "a", [], [], 400, 400);
  assert.equal(m.floor, 0);
  assert.ok(!("walls" in m) || m.walls.length === 0);
});
