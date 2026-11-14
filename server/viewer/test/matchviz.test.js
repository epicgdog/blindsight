import test from "node:test";
import assert from "node:assert/strict";
import { rankRows, footprintPaths, placeName, thumbUrl, simPercent } from "../logic.js";

const info = {
  "pano-1": { room: "Hallway", label: "0" },
  "pano-2": { room: "Other / Hallway", label: "34" },
};

test("placeName prefers room, falls back to scan label then id", () => {
  assert.equal(placeName("pano-1", info), "Hallway");
  assert.equal(placeName("pano-9", { "pano-9": { label: "7" } }), "Scan 7");
  assert.equal(placeName("abcdefghijk", {}), "abcdefgh");
});

test("thumbnail url points at the place's horizon face", () => {
  assert.equal(thumbUrl("pano-1"), "/data/pano/pano-1/face_2.jpg");
});

test("similarity bar percent is clamped to 0..100", () => {
  assert.equal(simPercent(0.91), 91);
  assert.equal(simPercent(1.4), 100);
  assert.equal(simPercent(-0.2), 0);
});

test("rankRows keeps server order and marks the locked pano", () => {
  const match = { ranking: [
    { place: "pano-2", sim: 0.8, ref: 1, inliers: 5 },
    { place: "pano-1", sim: 0.6, ref: 2, inliers: 40 },
  ] };
  const rows = rankRows(match, "pano-1", info);
  assert.deepEqual(rows.map((r) => r.place), ["pano-2", "pano-1"]);
  assert.deepEqual(rows.map((r) => r.here), [false, true]);
  assert.equal(rows[0].name, "Other / Hallway");
  assert.equal(rows[0].percent, 80);
  assert.equal(rows[0].rank, 1);
  assert.equal(rows[0].scan, "34");
  assert.equal(rows[1].scan, "0");
});

test("rankRows tolerates a missing match", () => {
  assert.deepEqual(rankRows(null, "pano-1", info), []);
  assert.deepEqual(rankRows({}, "pano-1", info), []);
});

test("footprintPaths scales to the image and repeats at +/-1 for the seam", () => {
  const poly = [[-0.05, 0.4], [0.05, 0.4], [0.05, 0.6], [-0.05, 0.6]];
  const paths = footprintPaths(poly, 2048, 1024);
  assert.equal(paths.length, 3);
  const middle = paths[1].split(" ").map((p) => p.split(",").map(Number));
  assert.deepEqual(middle[0], [-102.4, 409.6]);
  const right = paths[2].split(" ")[0].split(",").map(Number);
  assert.equal(right[0], 2048 - 102.4);
});

test("footprintPaths returns nothing without a polygon", () => {
  assert.deepEqual(footprintPaths(null, 100, 50), []);
  assert.deepEqual(footprintPaths([], 100, 50), []);
});

import { yawText, errorText } from "../logic.js";

test("yawText shows unknown instead of 0 degrees when the server has no heading", () => {
  assert.equal(yawText(null), "–");
  assert.equal(yawText(undefined), "–");
  assert.equal(yawText(NaN), "–");
  assert.equal(yawText(0), "0°");
  assert.equal(yawText(328.4), "328°");
});

test("errorText turns server errors into a visible message", () => {
  assert.equal(errorText(409, { detail: "Connect a frame source before starting warm-up" }),
    "Connect a frame source before starting warm-up");
  assert.equal(errorText(409, null), "Request refused (409)");
  assert.equal(errorText(500, { detail: [{ msg: "bad" }] }), "Server error (500)");
  assert.equal(errorText(200, {}), "");
});
