# Metric navigation integration

Part 1 now exposes POST /api/navigation/route and /api/navigation/guide. They are
an opt-in foundation, not validated independent walking guidance. Existing camera
frames and panorama matches do not provide a metric phone pose. No real property
map or phone tracking has been fabricated. Missing maps return HTTP 503.

Create data/navigation.json from verified property geometry. Coordinates are meters,
x east, y north, heading clockwise from north. Use a unique map_id for every map
revision. Nodes have id, label, x_m, y_m and floor (string). Edges have source,
target, surveyed_clear (default false) and stairs (default false). Edges are
bidirectional straight, surveyed corridors on one floor. Place nodes at every bend,
door and stopping point. Only surveyed, stair-free edges are eligible. Scan adjacency
alone is insufficient: a line can cross furniture or a wall. Survey clearance for
the intended user and device; the current corridor threshold is not a measured
clearance or obstacle detector. Multilevel routes are intentionally unsupported.

POST /api/navigation/route with {"start":"entry","destination":"kitchen"}
returns map_id, the ordered route IDs, and distance_m (Dijkstra shortest path).
A one-node route means start equals destination and requires no walking instruction.

POST /api/navigation/guide requires map_id, route, segment (zero-based, initially 0),
x_m, y_m, floor, heading_deg, position_uncertainty_m, heading_uncertainty_deg,
pose_age_ms, tracking ("normal"), stride_m, stride_calibrated and hazard.
The pose must already be registered into the map frame. Uncertainty and pose age
must come from the tracking integration, never invented defaults. Call repeatedly
with fresh pose. Stop states suppress step estimates. Turns precede forward
instructions. WAYPOINT tells the client to stop and advance to next_segment; ARRIVED
ends the route. Do not advance segment solely from counted steps.

Remaining steps are ceil(distance to next waypoint / measured personal stride),
recomputed from position; they are estimates, not an exact future count. No calibrated
stride means no step count. Actual observed steps should be counted separately on
the phone. Arrival requires the entire supplied uncertainty radius within 0.4 m.
Pose older than 500 ms, uncertainty over 0.35 m, heading uncertainty over 15 degrees,
wrong floor, reported hazard, or leaving the 0.75 m centerline corridor stops guidance.
These are conservative prototype thresholds, not established accuracy guarantees.

Part 2 integration: native visual-inertial pose (e.g. ARKit), timestamp/uncertainty,
motion-based observed steps, personal stride calibration, destination selection,
route/segment state, and accessible speech. Immediately cancel forward speech for
hazards/tracking loss/disconnection; expire every instruction when fresh poses stop.
Do not queue stale guidance. This API does not currently inject navigation into the
existing WebSocket cue scheduler; the app must arbitrate it below hazard cues.

Part 3 integration: export surveyed whole-property nodes/corridors and align native
tracking coordinates to the model with metric correspondences. A single panorama
and heading do not establish metric translation. Refresh alignment and propagate
uncertainty as tracking drifts. Required before a real tour: measured position and
heading error, route/clearance verification, actual-device speech/step tests, and
supervised walkthroughs including tracking loss, obstacles and network failure.
