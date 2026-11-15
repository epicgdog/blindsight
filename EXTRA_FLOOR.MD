# Extra floor plan

Goal: the phone map and the guidance work on the top floor as well as the ground floor.

## Where things stand

- The scan has 3 levels (`server/minimap.py` splits scan points by height):
  - floor 0 (ground): 36 scan points, mean height 1.37 m
  - floor 1 (stair landing): 1 scan point, 3.36 m
  - floor 2 (top): 29 scan points, 5.37 m
- The localizer already matches against all 66 scan points.
- Everything after the localizer is ground floor only:
  - walls: `data/matterport/walls_floor1.json` is one slice of the mesh at z = 1.0 m
  - `scan_points()` returns walls for floor 0 only (`floor_walls(data_dir, pts, 0)`)
  - routing: `EntranceMap.load` keeps only floor 0 points (`server/entrance_nav.py:188`)
  - tracker: a fix more than 1.2 m higher or lower is rejected as "another floor"
    (`FLOOR_DZ_M`, `server/tracker.py:26`) until 3 consistent fixes restart the track
  - viewer and phone map draw floor 0 only
- The entrance (scan 28) is on floor 0.

## Phase A: show the right floor (small)

No change to guidance. The map follows the person onto the top floor.

1. **Walls for the top floor.** Slice the mesh 1.0 m above that floor's ground
   (floor z 5.37 m minus camera height 1.4 m, plus 1.0 m, so about z = 4.97):

   ```
   uv run python3 -m server.walls --obj data/matterport/R1FX6EmdcDt/50k_out_res_50k.obj \
       --z 4.97 --floor 2 --out data/matterport/walls_floor3.json
   ```

   Check the slice on the viewer before trusting it: a slice that cuts through furniture or
   the stair opening gives false walls.
2. **Server.** `scan_points()` returns walls per floor: `walls_by_floor: {"0": [...], "2": [...]}`.
   Keep `walls` (floor 0) so the web viewer does not break.
3. **Which floor is the person on.** One pure function, used by phone and viewer:
   - tracked `position.z` when there is one: nearest floor by height
   - else the floor of the decided place
   - else floor 0
4. **Phone map.** Draw the chosen floor's walls and points. Add a floor chip ("Floor 1",
   "Floor 3"). Route line only when the person is on the route's floor.
5. **Banner off the ground floor.** "Go to the ground floor" (no route yet in this phase).
6. **Web viewer.** Same floor switch in `wallMarkers` (it has `floor = 0` fixed).

Tests: floor choice from z, walls per floor in `scan_points`, map markers for floor 2.

## Phase B: route across floors (medium to large)

1. **One graph over all floors.** Nodes keep their floor. Edges inside a floor as today
   (distance limit, no wall crossing, per floor walls). Stair edges: landing point (floor 1)
   to its nearest point on floor 0 and on floor 2, written by hand in a small json
   (`data/matterport/stairs.json`), not guessed from distance.
2. **Stairs maneuver.** `Route` gets a new maneuver kind: "stairs down" / "stairs up". The
   spoken instruction is fixed: "Stairs ahead. Go down one flight and hold the rail."
3. **Guidance on the stairs.** Pause turn-by-turn while on the stairs: position there is poor
   (one scan point). Resume and replan on the first accepted fix on the new floor.
4. **Tracker.** When the last fix was within about 3 m of a stair point, accept a floor
   change on the first consistent fix, not the third.
5. **Agent.** `context_line` and `get_location` say the floor. `plan_route` legs carry the
   stairs leg. System prompt: one line on how to speak a stairs instruction.
6. **Map.** Route drawn for the current floor only, with a stairs icon where it leaves the
   floor. Bottom bar shows total meters over both floors.

Tests: graph reachability from every top floor point to the entrance, route with a stairs
leg, guidance pause and resume, tracker floor change near stairs and away from stairs.

## Risks

- **Safety.** A blind person on a staircase with weak position tracking. The stairs
  instruction must not depend on live position, and must come before the first step, not on it.
- **Landing has one scan point.** Localization on the stairs will often be lost. Phase B must
  work with no fix between the top of the stairs and the bottom.
- **Hazard detection on stairs.** The object detector has no "stairs" class. A close-obstacle
  warning will not fire for a step edge.
- **Wall slice quality** on the top floor is unknown until it is drawn.
- **Needs an on-site test** before it is used with a real user: walk down and up at least 5
  times, check when the floor switches and when the stairs instruction is spoken.

## Order

Phase A after the phone map screen exists. Phase B only after Phase A is tested on site.
