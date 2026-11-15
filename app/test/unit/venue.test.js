import test from "node:test";
import assert from "node:assert/strict";
import {
  distanceText, fitBox, imageUrl, mapToPx, pxToMap, shownLevel, stepArrow, touchToPx, transferMarks, venueBanner,
  venueDestUrl, venueInfoUrl, venueMarkUrl, venueRouteUrl, venueSearchUrl,
} from "../../src/venue.js";

const WS = "wss://example.ts.net/ws/phone";
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

test("venue URLs come from the websocket address", () => {
  assert.equal(venueInfoUrl(WS), "https://example.ts.net/api/venue");
  assert.equal(venueSearchUrl(WS, "judges room"), "https://example.ts.net/api/venue/search?q=judges%20room");
  assert.equal(venueRouteUrl(WS, "A98"), "https://example.ts.net/api/venue/route?to=A98");
  assert.equal(venueDestUrl(WS), "https://example.ts.net/api/agent/venue");
  assert.equal(venueMarkUrl(WS), "https://example.ts.net/api/venue/mark");
  assert.equal(imageUrl(WS, "/venue/level_1.png"), "https://example.ts.net/venue/level_1.png");
  assert.equal(venueSearchUrl(WS, ""), null);
  assert.equal(venueInfoUrl(""), null);
});

test("shown level: picked by hand, else where you are, else where the route starts, else ground", () => {
  const route = { ok: true, legs: [{ level: "1" }, { level: "A" }] };
  assert.equal(shownLevel({ override: "2", you: { level: "1" }, route }), "2");
  assert.equal(shownLevel({ override: null, you: { level: "2" }, route }), "2");
  assert.equal(shownLevel({ override: null, you: null, route }), "1");
  assert.equal(shownLevel({ override: null, you: null, route: null }), "1");
});

test("plan pixels and map metres round trip", () => {
  const align = { scale: 7.5, rot: 0, tx: 491.81, ty: 408.55 };
  const [u, v] = mapToPx(align, 3.2, -7.5);
  near(u, 491.81 + 24); near(v, 408.55 + 56.25);
  const [x, y] = pxToMap(align, u, v);
  near(x, 3.2); near(y, -7.5);
  const rot = { scale: 7.5, rot: 12, tx: 100, ty: 200 };
  const [x2, y2] = pxToMap(rot, ...mapToPx(rot, -4, 9));
  near(x2, -4); near(y2, 9);
});

test("a touch maps back to plan pixels through the contain fit", () => {
  const box = { width: 400, height: 600 };
  const f = fitBox(box, 1554, 1008);
  near(f.scale, 400 / 1554);
  const [u, v] = touchToPx(box, 1554, 1008, f.offX + 777 * f.scale, f.offY + 504 * f.scale);
  near(u, 777); near(v, 504);
  assert.equal(touchToPx(box, 1554, 1008, 200, 5), null); // in the letterbox above the plan
});

test("banner arrows from spoken steps", () => {
  assert.equal(stepArrow("Turn left, then walk about 8 meters."), "left");
  assert.equal(stepArrow("Turn slightly right, then walk about 3 meters."), "slight-right");
  assert.equal(stepArrow("Turn around, then walk about 10 meters."), "uturn");
  assert.equal(stepArrow("Walk straight ahead about 10 meters."), "straight");
  assert.equal(stepArrow("The restroom is straight ahead."), "arrive");
  assert.equal(stepArrow("Walk about 10 meters."), "straight");
  assert.equal(stepArrow("Use the north stairs by the restrooms down to level A, one floor below ground."), "straight");
  assert.equal(stepArrow("Judges and mentors room A98 is the door on your right."), "arrive");
});

test("venue banner shows the next step and where the room is", () => {
  const route = { ok: true, steps: ["Turn left, then walk about 8 meters.", "x"], to: { id: "A98", level_name: "Level A" } };
  assert.deepEqual(venueBanner(route), { title: "Turn left, then walk about 8 meters.", meters: "A98 · Level A", arrow: "left", tone: "go" });
  assert.equal(venueBanner({ ok: false }), null);
  assert.equal(venueBanner(null), null);
});

test("transfer marks: leaving on one level, arriving on the next", () => {
  const route = {
    ok: true,
    legs: [{ level: "1", px: [[0, 0], [10, 0]] }, { level: "A", px: [[5, 5], [6, 9]] }],
    transfers: [{ from_level: "1", to_level: "A", kind: "stairs" }],
  };
  assert.deepEqual(transferMarks(route, "1"), [{ px: [10, 0], kind: "stairs", to: "A", going: true }]);
  assert.deepEqual(transferMarks(route, "A"), [{ px: [5, 5], kind: "stairs", to: "1", going: false }]);
  assert.deepEqual(transferMarks(route, "2"), []);
});

test("distance text", () => {
  assert.equal(distanceText(70.7), "71 m");
  assert.equal(distanceText(0.2), "1 m");
  assert.equal(distanceText(null), "");
});
