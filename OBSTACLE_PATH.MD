# Path that bends around detected objects (YOLO + route)

Goal: objects the camera sees (chair, person, suitcase, ...) are placed on the floor map, and
the route shifts around them, on the phone map and in the spoken guidance.

## Where things stand

- YOLO runs on every frame on the server (`server/detect.py`, `detect_onnx.py`, COCO classes).
- `server/cues.py describe()` turns a box into words only:
  - direction from the box centre ("ahead", "slightly left", "left"), plus `bearing_deg`
  - distance from the box area ("very close", "close"); `distance_m` is always `None` and
    `distance_source` is `"image_area_proxy"`. The fields for a real distance exist, unused.
- `AgentState._hazards()` says "Stop. chair very close, ahead." No position, no route change.
- `SceneMemory` remembers class + direction words per place for 60 s. No coordinates.
- The route (`server/route.py Route`) is a fixed polyline over scan points, about 4 m apart,
  checked only against walls. Furniture is not in the wall data.
- What is known per frame: tracked position (x, y, z, sigma), fused heading (deg, sigma),
  phone pitch and roll, camera intrinsics (assumed 55 degree field of view until calibrated).

## The hard part: where is the object, in metres?

A box in the image has no depth. Three ways to get it, in order of cost:

| Method | How | Good for | Weak at |
|---|---|---|---|
| **Floor contact (chosen for phase 1)** | The bottom centre of the box is where the object meets the floor. Cast that pixel's ray using pitch, roll and camera height; it hits the floor plane at a range and bearing. | Things standing on the floor within about 5 m | Box cut off by the bottom of the image (too close), objects on tables, pitch errors far away |
| Depth model | A monocular depth network on the server, scaled by the floor | Any object | Extra 50 to 150 ms per frame, scale drift |
| Phone depth (LiDAR / ARKit) | Depth from the native module (`app/modules/blindsight-tracking`) | Best accuracy | Pro iPhones only, native path only |

Error to expect from floor contact, camera at 1.4 m: a 2 degree pitch error gives about
±0.15 m at 2 m and ±0.45 m at 4 m. A 10 degree heading error moves the object 0.5 m sideways
at 3 m. So the map position of an object is only as good as the heading.

## Design

Two tiers, because the map position is often not good enough:

**Tier 1: avoid relative to the body (no map needed).**
Range and bearing from the camera are enough to know an object is in the walking corridor
(within 0.5 m of the line straight ahead, closer than 3 m). Then say which side is free:
"Chair ahead, step a little to your left." Works with no position fix at all.

**Tier 2: put it on the map and reshape the route.**
Used only when position sigma is at most 1.0 m and heading sigma at most 15 degrees.

### Obstacle map (`server/obstacles.py`, new)

```
Obstacle: cls, xy (map metres), radius_m (from class: chair 0.4, table 0.8, person 0.35, ...),
          sigma_m, first_t, last_t, hits, misses, moving (person, bicycle)
```

- **Add / merge.** A new measurement joins the nearest obstacle of the same class within a
  gate (1 m, wider when sigma is large); its position is averaged in, weighted by sigma.
- **Confirm.** An obstacle affects the route only after 2 hits (same rule as `HAZARD_FRAMES`).
- **Forget.** Static classes stay until contradicted: the spot is inside the camera's view
  and range, and nothing was detected there 3 frames in a row. Unseen static obstacles
  expire after 5 minutes. Moving classes expire after 3 s.
- **Latency.** A frame is 0.3 to 1 s old when its result is used. Place the object from the
  position and heading at the frame's capture time (`ts_ms` is already sent), not from now.

### Reshaping the route

1. **Find conflicts.** For every confirmed static obstacle, the distance from its centre to
   the route ahead. Conflict when below `radius + 0.45 m` (half a body plus margin).
2. **Local detour (first choice).** Add up to two waypoints beside the obstacle, on the side
   with more room, so the route passes at `radius + 0.45 m`. Each new segment must pass
   `clear_line` against the walls and against the other obstacles.
3. **Replan (second choice).** Neither side is clear: mark the graph edge as blocked and run
   Dijkstra again. With scan points 4 m apart there is often no other way.
4. **Blocked (last).** No route: stop guidance for this spot and say it: "Something is
   blocking the way. Wait, or ask someone nearby."
5. **People.** Never reshape around a moving person. Within 2 m in the corridor:
   "Someone is in front of you, wait a moment."
6. **Stability.** Reshape at most once per second. A detour stays until the person has passed
   the obstacle, even if the obstacle drops out of view (it will: the camera turns away).
   A shift under 0.3 m is ignored.

### Guidance and voice

- `Route` gets a new maneuver kind, a sidestep: "veer left around the chair". Today a
  maneuver needs a 30 degree turn and a 1 m leg (`TURN_DEG`, `MIN_LEG_M`), so a small detour
  would be silent.
- New EVENT kind `[obstacle]`: `chair 2 m ahead on the route: veer left, then straight`.
  One per obstacle, not per frame. `[urgent]` stays as it is for very close objects.
- A detour that only moves the line (no new instruction needed) is not spoken.
- `get_surroundings` and the CONTEXT line carry real distances ("chair, 2 m, slightly left").
- System prompt: one line on how to speak `[obstacle]`.

### Phone map and debug

- `snapshot()` adds `obstacles: [{cls, x, y, radius_m, confirmed, moving}]`.
- The map draws them as grey discs with a small icon; the blue route bends around them.
  Unconfirmed ones are faint.
- Debug sheet: a list of obstacles with range, bearing, sigma, hits, and which tier is active
  and why ("tier 1: heading ±22°").

## Steps

1. **Range from the floor (`server/obstacles.py`, pure functions + tests).**
   `floor_range(bbox, K, pitch, roll, cam_height)` returns range, bearing and a sigma; returns
   nothing when the box bottom touches the image bottom or the ray points above the horizon.
   Fill `distance_m` and `distance_source = "floor_contact"` in `describe()`.
2. **Tier 1.** Corridor check and free-side choice in `AgentState`; the `[obstacle]` event.
   No map position needed.
3. **Obstacle map.** Add, merge, confirm, forget, with capture-time pose.
4. **Route reshaping** in `entrance_nav.py` / `route.py`: conflicts, local detour, replan,
   blocked; the sidestep maneuver.
5. **Snapshot + phone map + debug sheet.**
6. **Evaluation tool** (`tools/eval_obstacles.py`): replay a recorded session and report the
   estimated object positions against measured ones.

## Tests

- floor range: known pitch and box give the right range; cut-off box and above-horizon cases
- obstacle map: merge, confirm after 2 hits, forget when looked at and gone, moving expiry
- reshaping: detour side with more room; wall on one side; both sides blocked gives replan;
  no route gives blocked; a person never reshapes; no flicker when the obstacle position jitters by 0.2 m
- guidance: one `[obstacle]` event per obstacle; silent small shifts; detour kept after the
  obstacle leaves the view

## Limits and risks

- **YOLO sees only its classes.** No stairs, glass doors, boxes, carts, wet floor signs, poles.
  This reduces collisions; it does not replace the cane.
- **Furniture that is always there** (it was in the room when it was scanned) will be detected
  again and again. That is fine: the route then simply avoids it.
- **Field of view is still assumed 55 degrees.** Range and bearing both depend on it. Calibrate
  (`make p3-hfov`) before trusting tier 2.
- **Camera height** is assumed 1.4 m (chest). A phone held lower gives ranges that are too long.
  Use the tracked z above the floor when it is sure.
- **2 frames per second.** At walking speed the person moves 0.5 m between frames. Tier 1
  warnings must fire at 3 m, not at 1 m.
- **A wrong detour is worse than none** when it steers a blind person toward a wall. Every
  detour segment is checked against the walls, and tier 2 is off when the heading is unsure.
- **Needs on-site tests:** put a chair on the route at 5 known spots, compare the map
  position with a tape measure, and walk each detour with a sighted person first.

## Order

Steps 1 and 2 first: they are useful alone and need no map position. Steps 3 to 5 after the
phone map screen exists and the field of view is calibrated. Works with `DROP_PIN.MD`
(any destination) and stays ground floor only until `EXTRA_FLOOR.MD` phase B.
