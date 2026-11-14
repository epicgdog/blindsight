import test from "node:test";
import assert from "node:assert/strict";
import { planPoint, planViewBox, mapMarkers } from "../logic.js";

// 100 px per meter, origin (-10, -5), image 2000 x 1000. y is flipped (image y grows downward).
const plan = { url: "/api/floorplan/0", floor: 0, width: 2000, height: 1000, resolution: 100, origin: { x: -10, y: -5 } };

test("world coordinates map to plan pixels with y flipped", () => {
  assert.deepEqual(planPoint({ x: -10, y: -5 }, plan), { x: 0, y: 1000 });   // bottom-left corner
  assert.deepEqual(planPoint({ x: 0, y: 0 }, plan), { x: 1000, y: 500 });
  assert.deepEqual(planPoint({ x: 10, y: 5 }, plan), { x: 2000, y: 0 });     // top-right corner
});

test("view box frames the points with padding and stays inside the image", () => {
  const pts = [{ x: -1, y: -1 }, { x: 1, y: 1 }];
  const vb = planViewBox(pts, plan, 100);
  assert.deepEqual(vb, { x: 800, y: 300, w: 400, h: 400 });
  const edge = planViewBox([{ x: -10, y: -5 }], plan, 300);
  assert.ok(edge.x >= 0 && edge.y >= 0 && edge.x + edge.w <= 2000 && edge.y + edge.h <= 1000);
});

const map = {
  plan,
  points: [
    { id: "a", x: 0, y: 0, floor: 0, room: "Hallway" },
    { id: "b", x: 4, y: 2, floor: 0, room: null, label: "7" },
    { id: "u", x: 1, y: 1, floor: 1, room: "Upper" },
  ],
  bounds: { min_x: 0, max_x: 4, min_y: 0, max_y: 2 },
};

test("with a plan, markers use plan pixels and only floor 1 is shown", () => {
  const m = mapMarkers(map, "a", [{ place: "b", sim: 0.5 }], [], 400, 400);
  assert.equal(m.floor, 0);
  assert.deepEqual(m.markers.map((k) => k.id).sort(), ["a", "b"]);
  const a = m.markers.find((k) => k.id === "a");
  assert.deepEqual([a.x, a.y], [1000, 500]);
  assert.ok(m.viewBox && m.viewBox.w > 0);
  assert.equal(m.plan.url, "/api/floorplan/0");
  assert.equal(m.otherFloor, false);
});

test("a place on another floor is flagged, not drawn", () => {
  const m = mapMarkers(map, "u", [], [], 400, 400);
  assert.equal(m.otherFloor, true);
  assert.ok(!m.markers.some((k) => k.kind === "active"));
  assert.equal(m.name, "Upper");
});
