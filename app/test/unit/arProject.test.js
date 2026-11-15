import test from "node:test";
import assert from "node:assert/strict";
import { AR, arScene, focalPx, pathAhead, project, toCamera, wrap180 } from "../../src/arProject.js";

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);
const CAM = { width: 400, height: 800, pitchDeg: -25, rollDeg: 0, heightM: 1.4, vfovDeg: 67 };

test("map point into the camera frame: forward and right from the heading", () => {
  let c = toCamera([0, 5], [0, 0], 0); // facing north, point north
  near(c.f, 5); near(c.r, 0);
  c = toCamera([2, 0], [0, 0], 0); // east is to the right when facing north
  near(c.f, 0); near(c.r, 2);
  c = toCamera([0, 3], [0, 0], 90); // facing east, north is to the left
  near(c.f, 0); near(c.r, -3);
});

test("a floor point on the line of sight lands in the screen centre", () => {
  const f = 1.4 / Math.tan((25 * Math.PI) / 180); // looking 25 degrees down from 1.4 m
  const [u, v] = project(f, 0, 0, CAM);
  near(u, 200); near(v, 400);
  assert.ok(project(f + 2, 0, 0, CAM)[1] < 400, "further along the floor is higher on screen");
  assert.ok(project(f, 1, 0, CAM)[0] > 200, "to the right is right on screen");
  assert.equal(project(-1, 0, 0, CAM), null, "behind the camera is not drawn");
});

test("turning the phone clockwise turns the drawing the other way", () => {
  const far = project(6, 0, 0, CAM), rolled = project(6, 0, 0, { ...CAM, rollDeg: 10 });
  assert.ok(far[1] < 400 && rolled[0] < far[0], "a point above the centre moves left when the phone turns clockwise");
});

test("the path ahead stops a short stub past the first corner", () => {
  const route = [[0, 0], [0, 6], [8, 6]]; // north 6 m, then right
  const a = pathAhead(route, [0, 1]);
  near(a.length, 5 + AR.stubM);
  assert.ok(a.corner && a.corner.turn > 0, "the corner is a right turn");
  assert.deepEqual(a.pts[a.pts.length - 1].map((v) => Math.round(v * 10) / 10), [2.5, 6]);
  assert.equal(a.end, false);
  const short = pathAhead([[0, 0], [0, 4]], [0, 0]);
  assert.ok(short.end && short.corner === null);
});

test("a straight path ahead is drawn up the middle of the screen with chevrons and the pin", () => {
  const sc = arScene({ route: [[0, 0], [0, 8]], pos: [0, 0], headingDeg: 0, cam: CAM });
  assert.ok(sc.quads.length > 5 && sc.chevrons.length >= 3);
  for (const q of sc.quads) {
    const mid = q.pts.reduce((s, p) => s + p[0], 0) / 4;
    near(mid, 200, 1e-6);
  }
  assert.ok(sc.pin && sc.pin.top[1] < sc.pin.base[1]);
  assert.equal(sc.hint, null);
});

test("a path out of view gives a hint to turn", () => {
  const right = arScene({ route: [[0, 0], [8, 0]], pos: [0, 0], headingDeg: 0, cam: CAM });
  assert.equal(right.hint, "right");
  const left = arScene({ route: [[0, 0], [-8, 0]], pos: [0, 0], headingDeg: 0, cam: CAM });
  assert.equal(left.hint, "left");
  const behind = arScene({ route: [[0, 0], [0, -8]], pos: [0, 0], headingDeg: 0, cam: CAM });
  assert.equal(behind.hint, "behind");
});

test("no heading, no route: nothing drawn", () => {
  assert.equal(arScene({ route: [[0, 0], [0, 5]], pos: [0, 0], headingDeg: null, cam: CAM }).quads.length, 0);
  assert.equal(arScene({ route: null, pos: [0, 0], headingDeg: 0, cam: CAM }).quads.length, 0);
});

test("helpers", () => {
  near(wrap180(190), -170); near(wrap180(-190), 170); near(wrap180(45), 45);
  near(focalPx(800, 90), 400);
});
