import test from "node:test";
import assert from "node:assert/strict";
import {
  accuracyText, banner, bottomBar, boxStyle, durationText, entrancePoint, fitView, floorExtent, followAnchor, followView,
  framePosition, frameMatch, lerpAngle, lerpView, leftText, mapLayout, mapMode, metersText, metricsUrl, navUrl, nextArrow,
  panoUrl, project, rankRows, routePoints, screenAngle, tiltPoint, timeLeftS, turnArrow, untiltY, viewMatrix, wallPath, whyLines,
} from "../../src/map.js";

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);
const nearPt = (p, q) => { near(p[0], q[0]); near(p[1], q[1]); };

// Apply an SVG matrix(a b c d e f) string to a point.
const applyMatrix = (m, x, y) => {
  const [a, b, c, d, e, f] = m.slice(7, -1).split(" ").map(Number);
  return [a * x + c * y + e, b * x + d * y + f];
};

test("north-up view: north is up, east is right, same scale both ways", () => {
  const v = { cx: 0, cy: 0, rot: 0, scale: 10, ax: 100, ay: 200 };
  nearPt(project(v, 0, 0), [100, 200]);
  nearPt(project(v, 1, 0), [110, 200]);
  nearPt(project(v, 0, 1), [100, 190]);
});

test("heading-up view turns the facing direction to the top of the screen", () => {
  const anchor = { x: 50, y: 80 };
  const east = followView({ x: 2, y: 3 }, 90, anchor, 10);
  nearPt(project(east, 2, 3), [50, 80]); // you at the anchor
  nearPt(project(east, 3, 3), [50, 70]); // a metre east (ahead) is straight up
  nearPt(project(east, 2, 2), [60, 80]); // south is on your right when facing east
  const south = followView({ x: 0, y: 0 }, 180, anchor, 10);
  nearPt(project(south, 0, -1), [50, 70]);
  nearPt(project(south, -1, 0), [60, 80]); // west is on your right when facing south
  assert.equal(screenAngle(east, 90), 0);
  assert.equal(screenAngle({ rot: 0 }, 45), 45);
});

test("the SVG matrix maps points exactly like project()", () => {
  for (const v of [{ cx: 1, cy: 2, rot: 0, scale: 5, ax: 10, ay: 20 }, { cx: -3, cy: 7, rot: 137, scale: 30, ax: 400, ay: 900 }]) {
    const m = viewMatrix(v);
    for (const [x, y] of [[0, 0], [4, -2], [-5, 9]]) {
      const a = applyMatrix(m, x, y), b = project(v, x, y);
      near(a[0], b[0], 1e-2); near(a[1], b[1], 1e-2);
    }
  }
});

test("overview fits the whole floor into the screen region", () => {
  const map = { points: [{ id: "a", x: 0, y: 0, floor: 0 }, { id: "b", x: 20, y: 6, floor: 0 }, { id: "c", x: 99, y: 99, floor: 1 }] };
  const ext = floorExtent(map);
  assert.deepEqual(ext, { minX: -2, maxX: 22, minY: -2, maxY: 8 }); // floor 0 only, 2 m margin
  const region = { x: 100, y: 200, w: 300, h: 600 };
  const v = fitView(ext, region, 0);
  const [x1, y1] = project(v, ext.minX, ext.maxY), [x2, y2] = project(v, ext.maxX, ext.minY);
  near(x1, 100); near(x2, 400); // width is the tight side
  assert.ok(y1 >= 200 && y2 <= 800);
  assert.equal(v.rot, 0);
  assert.equal(floorExtent({ points: [], bounds: { min_x: 1, max_x: 2, min_y: 3, max_y: 4 } }).maxY, 4);
  assert.equal(fitView(null, region), null);
});

test("follow only with a position and a known facing", () => {
  assert.equal(mapMode("follow", { x: 1, y: 2 }, 30), "follow");
  assert.equal(mapMode("follow", { x: 1, y: 2 }, null), "overview");
  assert.equal(mapMode("follow", null, 30), "overview");
  assert.equal(mapMode("north", { x: 1, y: 2 }, 30), "overview");
});

test("tilt pulls the top away and untilt inverts it", () => {
  const p = 1000;
  const [, top] = tiltPoint(0, -300, 50, p), [, bottom] = tiltPoint(0, 300, 50, p);
  assert.ok(-top < 300 * Math.cos(50 * Math.PI / 180)); // far side shrinks
  assert.ok(bottom > 300 * Math.cos(50 * Math.PI / 180)); // near side grows
  for (const ys of [-200, 0, 150, 400]) near(tiltPoint(0, untiltY(ys, 50, p), 50, p)[1], ys, 1e-6);
  near(tiltPoint(7, 0, 50, p)[0], 7);
});

test("the follow anchor lands in the lower third of the screen after tilt", () => {
  const L = mapLayout(390, 700);
  assert.deepEqual([L.lw, L.lh, L.left, L.top], [546, 1680, -78, -490]);
  assert.deepEqual(L.region, { x: 78, y: 490, w: 390, h: 700 });
  // the tilted layer still covers the screen's top corners
  const vTop = untiltY(-350, 50, L.perspective);
  assert.ok(-vTop < L.lh / 2);
  assert.ok(tiltPoint(L.lw / 2, vTop, 50, L.perspective)[0] >= 390 / 2);
  const a = followAnchor(L);
  const [, ys] = tiltPoint(0, a.y - L.lh / 2, 50, L.perspective);
  near(ys + 700 / 2, 700 * 2 / 3, 1e-6);
  near(a.x, L.lw / 2);
});

test("glide interpolates positions linearly and angles the short way", () => {
  near(lerpAngle(350, 10, 0.5), 360);
  near(lerpAngle(10, 350, 0.5), 0);
  near(lerpAngle(0, 170, 1), 170);
  const v = lerpView({ cx: 0, cy: 0, rot: 350, scale: 10, ph: 0 }, { cx: 10, cy: 4, rot: 30, scale: 20, ph: null }, 0.5);
  assert.deepEqual([v.cx, v.cy, v.scale, v.ph], [5, 2, 15, null]);
  near(v.rot, 370);
  assert.equal(lerpView(null, { cx: 1 }, 0.3).cx, 1);
});

test("turn arrows by size of the turn", () => {
  assert.equal(turnArrow(null), null);
  assert.equal(turnArrow(5), "straight");
  assert.equal(turnArrow(-30), "slight-left");
  assert.equal(turnArrow(90), "right");
  assert.equal(turnArrow(-130), "sharp-left");
  assert.equal(turnArrow(170), "uturn");
  assert.equal(turnArrow(-180), "uturn");
  assert.equal(nextArrow("turn left"), "left");
  assert.equal(nextArrow("turn around"), "uturn");
  assert.equal(nextArrow("the entrance"), "straight");
});

test("metres and time on screen, never steps", () => {
  assert.equal(metersText(4.4), "4 m");
  assert.equal(metersText(-1), "0 m");
  assert.equal(metersText(undefined), "");
  assert.equal(leftText(17.2), "17 m left");
  assert.equal(timeLeftS(25), 25);
  assert.equal(durationText(24.6), "25 s");
  assert.equal(durationText(61), "2 min");
  assert.deepEqual(bottomBar({ active: true, remaining_m: 17, remaining_steps: 23 }), { time: "17 s", left: "17 m left" });
  assert.deepEqual(bottomBar({ active: false }), { time: "", left: "" });
  assert.equal(accuracyText({ x: 0, y: 0, sigma_m: 0.42 }), "±0.4 m");
  assert.equal(accuracyText(null), "");
});

test("banner for every screen state", () => {
  const at = { x: 1, y: 1 };
  assert.equal(banner({ connected: false }).title, "Connecting…");
  assert.equal(banner({ connected: true, nav: { active: true }, position: null }).title, "Hold steady, finding you…");
  assert.equal(banner({ connected: true, nav: { active: true, paused: true, next: "turn left" }, position: at }).title, "Hold steady, finding you…");
  const go = banner({ connected: true, nav: { active: true, next: "turn left", next_m: 4.2, turn_deg: 5, next_steps: 6 }, position: at });
  assert.deepEqual(go, { title: "Turn left", meters: "4 m", arrow: "left", tone: "go" });
  const turnFirst = banner({ connected: true, nav: { active: true, next: "turn left", next_m: 4, turn_deg: 95 }, position: at });
  assert.deepEqual([turnFirst.title, turnFirst.arrow], ["Turn right", "right"]);
  const unknownFacing = banner({ connected: true, nav: { active: true, next: "turn right", next_m: 8 }, position: at });
  assert.deepEqual([unknownFacing.title, unknownFacing.meters], ["Turn right", "8 m"]);
  assert.equal(banner({ connected: true, nav: { active: true, next: "the entrance", next_m: 3 }, position: at }).title, "Continue to the entrance");
  assert.equal(banner({ connected: true, nav: { active: false, arrived: true } }).title, "You're at the entrance");
  assert.equal(banner({ connected: true, nav: { active: false } }).title, "Add a pin to choose where to go");
});

test("frame fields: position from the match or its debug copy", () => {
  const p = { x: 1, y: 2, z: 1.4, sigma_m: 0.3, source: "fix" };
  assert.equal(framePosition({ match: { position: p } }), p);
  assert.equal(framePosition({ match: null, localization_debug: { match: { position: p } } }), p);
  assert.equal(framePosition({ match: { position: { x: null } } }), null);
  assert.equal(frameMatch(null), null);
});

test("route, walls and entrance pin in map metres", () => {
  assert.equal(routePoints({ pts: [[0, 0], [1.234, 5]] }), "0,0 1.23,5");
  assert.equal(routePoints({ pts: [[0, 0]] }), null);
  assert.equal(wallPath({ walls: [[0, 0, 1, 0], [1, 0, 1, 2]] }), "M0 0L1 0M1 0L1 2");
  assert.equal(wallPath(null), "");
  assert.deepEqual(entrancePoint({ goal: [3, 4] }, null), { x: 3, y: 4 });
  assert.deepEqual(entrancePoint(null, { points: [{ id: "n0ppizy9u8xcadypd2bawceyc", x: 5, y: 6 }] }), { x: 5, y: 6 });
  assert.equal(entrancePoint(null, { points: [] }), null);
});

test("ranking rows: top five, named, the locked one marked", () => {
  const ranking = Array.from({ length: 7 }, (_, i) => ({ place: `p${i}`, sim: 0.9 - i / 10, inliers: 100 - i }));
  const rows = rankRows({ ranking }, "p1", { p0: { room: "Lobby" }, p1: { label: 12 } });
  assert.equal(rows.length, 5);
  assert.deepEqual([rows[0].name, rows[1].name, rows[1].here, rows[0].percent], ["Lobby", "Scan 12", true, 90]);
  assert.deepEqual(rankRows(null, null), []);
});

test("why lines explain an unmatched frame", () => {
  assert.deepEqual(whyLines(null), []);
  const lines = whyLines({ reason: "too_few_inliers", matched: false, best_inliers: 12, thresholds: { N_MIN: 30, GRID_CELLS_MIN: 4 }, jump_rejected: true });
  assert.equal(lines[0], "Reason: too few inliers");
  assert.match(lines[1], /best inliers 12 \(need 30\)/);
  assert.ok(lines.includes("Rejected: too far from the tracked position"));
});

test("server urls follow the websocket scheme and host", () => {
  assert.equal(navUrl("wss://box.ts.net/ws/phone", false), "https://box.ts.net/api/agent/nav?on=0");
  assert.equal(navUrl("ws://10.0.0.2:8000/ws/phone", true), "http://10.0.0.2:8000/api/agent/nav?on=1");
  assert.equal(panoUrl("ws://10.0.0.2:8000/ws/phone"), "http://10.0.0.2:8000/api/pano");
  assert.equal(metricsUrl("wss://box.ts.net/ws/phone"), "https://box.ts.net/metrics");
  assert.equal(navUrl(null, true), null);
});

test("detection boxes as percentages", () => {
  assert.deepEqual(boxStyle([0.1, 0.2, 0.5, 0.6]), { left: "10%", top: "20%", width: "40%", height: "40%" });
});
