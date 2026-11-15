# Drop a pin and navigate there

Goal: pick any spot on the ground floor (by touching the map, or by voice) and get the same
turn-by-turn guidance there that the entrance gets today.

## Where things stand

- The destination is one fixed scan point: `EntranceMap.goal` (scan 28, the entrance), set once
  at startup (`server/agent.py` `ENTRANCE_PANO`).
- Routing is Dijkstra over the ground floor scan points (`server/entrance_nav.py`): `plan()`
  routes from the person's nearest scan point to `emap.goal`, and the route ends exactly on
  that scan point.
- "The entrance" is written into the code in several places:
  - `plan()`, `describe_route()`, `EntranceGuide.start()` and `_instruction()` read `emap.goal`
  - `ARRIVED = "You're at the entrance."`, and `agent.py` detects arrival by that text
  - `route_sentence()`: "already at the entrance"
  - `context_line()`: "Path to the entrance from here", `get_location`: `is_entrance`
  - `start_navigation` tool: only accepts the entrance
  - `SYSTEM_PROMPT`: every navigation line says entrance
- `AUTO_NAV` starts guidance to the entrance as soon as the place is known.

## Design

**A destination is a point, not a scan point.**

```
Destination: xy (map metres), node (scan point the route ends its graph part on),
             label ("the entrance", "your pin", "the kitchen"), kind (entrance | pin | saved)
```

- Route = shortest path over the scan points to `node`, then one straight last leg from
  `node` to `xy`. The entrance is the case where `xy` is the scan point itself (no last leg).
- `node` = the scan point with the shortest total path among those that have a clear line to
  the pin (no wall crossed, at most `MAX_EDGE_M` away). This is the same rule the graph
  already uses for its edges.
- A pin with no such scan point (inside a wall, outside the building, in an unscanned room)
  is moved to the nearest scan point, and the phone shows the pin jump there. Never route
  through a wall to reach a pin.
- Arrival: within `ARRIVE_M` of `xy`, as today.

**The entrance stays the default.** Clearing the pin goes back to it. `AUTO_NAV` behaves as
today until a pin is set.

## Steps

### 1. Server routing (`server/entrance_nav.py`)

1. Add `Destination` and `EntranceMap.destination` (starts as the entrance).
   `set_destination(xy, label, kind)` picks `node` with the rule above and returns
   `{ok, xy, snapped, distance_m}`; `clear_destination()` restores the entrance.
2. `plan()` appends the last leg. `describe_route()` and `EntranceGuide` read
   `emap.destination` where they read `emap.goal` now.
3. Arrival text from the label: "You're at your pin." Mark arrival with a flag on the guide
   status, and have `agent.py` use the flag, not the text.
4. Setting a destination while guiding replans at once from the current position and resets
   the guide's spoken-prompt memory, so the first instruction of the new route is said.

### 2. Server API (`server/agent.py`)

- `POST /api/agent/destination` body `{x, y, label?}` or `{entrance: true}`:
  sets it, starts guidance, returns the result of `set_destination` plus the route preview
  (`pts`, `length_m`).
- `GET /api/agent/destination?x=&y=`: preview only (route and distance, nothing changes).
  The phone uses it for the confirm sheet.
- `snapshot()["path"]["goal"]` already carries the goal point; add `label` and `kind`.

### 3. Phone (`app/`, on top of the new map screen)

1. **Drop.** Long press on the map. In tilted heading-up mode a touch point is hard to turn
   back into map metres, so a long press first switches the map to flat north-up, then the
   touch is converted with the inverse of the projection in `app/src/map.js`
   (new pure function `screenToMap`, tested as the inverse of `mapToScreen`).
2. **Confirm sheet** from the bottom: "Dropped pin · 12 m" with Start and Cancel. The route
   preview is drawn dashed. If the server moved the pin, the pin animates to the new spot and
   the sheet says "Moved to the nearest reachable spot".
3. **Start** posts the destination; the map goes back to tilted heading-up; banner and bottom
   bar work as for the entrance. The pin is red (the entrance pin stays, smaller, grey).
4. **Exit** stops guidance and keeps the pin. A second control on the sheet, "Clear pin",
   returns to the entrance.
5. A tap on a scan point dot or on the entrance pin selects it as the destination the same way.

### 4. Voice agent

1. `SYSTEM_PROMPT`: "entrance" becomes "the destination" in the navigation lines, with the
   entrance as the default one. The `[started]` and `[arrived]` events carry the label.
2. `context_line()`: "Path to <label> from here".
3. `start_navigation(destination)`: accepts "entrance" and "pin" (the pin set on the phone).
4. Tell the agent when the destination changes: one EVENT line
   `[started] New destination: your pin, about 12 m. <first instruction>`.

### 5. Saved places by voice (second phase)

A blind user cannot drop a pin on a map. The voice path is the one they can use.

- Tool `save_place(name)`: stores the current tracked position under a name
  ("remember this spot as my desk"). Refuses when the position is not known well enough
  (sigma above `TRACK_SIGMA_M`) and says "hold the phone steady" in that case.
- Tool `navigate_to(name)`: sets that saved place as the destination. `list_places()` for
  "where can you take me?".
- Stored in `data/places.json` (name, x, y, floor, created). The phone map shows them as
  small labelled pins; a tap selects one.

## Tests

- routing: pin with a clear line gets a last leg; pin behind a wall is moved; pin on a scan
  point equals today's route; entrance route is unchanged (existing tests still pass)
- guide: arrival at a pin, replan when the destination changes mid-route
- API: set, preview, clear, bad input (non-number, far outside the map)
- agent: context line and events carry the label; `start_navigation("pin")` with no pin set
- phone: `screenToMap(mapToScreen(p))` returns `p`; confirm sheet text; pin moved case

## Limits and risks

- **Ground floor only** until `EXTRA_FLOOR.MD` phase B. A pin on another floor is refused.
- **The last leg is a straight line with only a wall check.** Furniture is not in the wall
  data, so the last few metres may point through a table. The close-obstacle warning still
  works, but the route does not go around it. Keeping the last leg short (`MAX_EDGE_M`, 4 m)
  limits this.
- **Pin accuracy is the map accuracy.** Position error (often 0.5 to 1.5 m) and the assumed
  55 degree field of view both shift where "arrived" is said.
- **Saved places depend on the position at save time.** A place saved with a poor fix is
  wrong forever, hence the refusal rule above.

## Order

1. Steps 1 and 2 (server), with tests. The web viewer already draws `path.goal`, so this can
   be checked in the browser with `curl` before any phone work.
2. Step 3 (phone) after the map screen is in.
3. Step 4 (agent wording).
4. Step 5 (saved places by voice).
