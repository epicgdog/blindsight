import test from "node:test";
import assert from "node:assert/strict";
import { mapProject, mapMarkers, floorOfPlace, pushTrail } from "../logic.js";

const map = {
  points: [
    { id: "a", x: 0, y: 0, floor: 0, room: "Hallway", label: "0" },
    { id: "b", x: 10, y: 0, floor: 0, room: "Stairs", label: "1" },
    { id: "c", x: 10, y: 5, floor: 0, room: null, label: "2" },
    { id: "u", x: 5, y: 2, floor: 1, room: "Upper", label: "9" },
  ],
  bounds: { min_x: 0, max_x: 10, min_y: 0, max_y: 5 },
  floors: [{ floor: 0, count: 3 }, { floor: 1, count: 1 }],
};

test("projection keeps the aspect ratio, pads, and puts north (larger y) at the top", () => {
  const p = mapProject(map.points, map.bounds, 400, 400, 20);
  assert.equal(p.a.x, 20);
  assert.equal(p.b.x, 380); // 10 m spans the padded width
  assert.ok(p.c.y < p.a.y, "larger y is higher on screen");
  const dx = p.b.x - p.a.x, dy = p.a.y - p.c.y;
  assert.ok(Math.abs(dx / dy - 2) < 1e-9, "10 m x 5 m keeps a 2:1 shape");
});

test("projection copes with a single point or no bounds", () => {
  const one = [{ id: "z", x: 3, y: 3, floor: 0 }];
  const p = mapProject(one, { min_x: 3, max_x: 3, min_y: 3, max_y: 3 }, 200, 200, 10);
  assert.ok(Number.isFinite(p.z.x) && Number.isFinite(p.z.y));
  assert.deepEqual(mapProject([], null, 100, 100, 5), {});
});

test("floorOfPlace finds the floor of a place, or null", () => {
  assert.equal(floorOfPlace(map.points, "u"), 1);
  assert.equal(floorOfPlace(map.points, "nope"), null);
});

test("markers show only the active floor, with the active place and ranked candidates", () => {
  const ranking = [{ place: "a", sim: 0.9 }, { place: "b", sim: 0.7 }, { place: "u", sim: 0.5 }];
  const m = mapMarkers(map, "a", ranking, [], 400, 400);
  assert.equal(m.floor, 0);
  const kinds = Object.fromEntries(m.markers.map((k) => [k.id, k.kind]));
  assert.equal(kinds.a, "active");
  assert.equal(kinds.b, "candidate");
  assert.equal(kinds.c, "point");
  assert.ok(!("u" in kinds), "points on other floors are hidden");
  assert.equal(m.name, "Hallway");
});

test("with no active place the map follows the best ranked place's floor", () => {
  const m = mapMarkers(map, null, [{ place: "u", sim: 0.6 }], [], 400, 400);
  assert.equal(m.floor, 1);
  assert.equal(m.markers.find((k) => k.id === "u").kind, "candidate");
  assert.equal(m.name, "");
});

test("trail marks recently visited places on the floor", () => {
  const m = mapMarkers(map, "a", [], ["c", "b"], 400, 400);
  const kinds = Object.fromEntries(m.markers.map((k) => [k.id, k.kind]));
  assert.equal(kinds.b, "trail");
  assert.equal(kinds.c, "trail");
  assert.equal(kinds.a, "active");
});

test("pushTrail keeps distinct recent places, newest last, bounded", () => {
  let t = [];
  for (const p of ["a", "b", "a", "c", "c", "d"]) t = pushTrail(t, p, 3);
  assert.deepEqual(t, ["a", "c", "d"]);
  assert.deepEqual(pushTrail(["a"], null, 3), ["a"]);
});

test("a place without a room name falls back to its scan label", () => {
  const m = mapMarkers(map, "c", [], [], 400, 400);
  assert.equal(m.name, "Scan 2");
});

test("missing map data gives an empty result", () => {
  assert.deepEqual(mapMarkers(null, "a", [], [], 100, 100), { floor: null, name: "", markers: [] });
});
