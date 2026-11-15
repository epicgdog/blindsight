# North star

**Type or say where you want to go, as a general term ("judges room", "sponsors", "food") or
as a room number ("A97"), and Insight takes you there, using the HackPrinceton venue map.**

If this works, we are done. Everything else (AR, 3D, obstacle bending) is extra.

## Done means

Standing at check-in in the JRR atrium (room 117), each of these works by typing and by voice:

| You enter | It resolves to | Where |
|---|---|---|
| "judges room" | A97 and A98, offers the nearer one | Level A |
| "judging" | A71 | Level A, Simpson side |
| "CoStar" or "sponsor room" | A01 (CoStar), then A02, A12 | Level A |
| "hacker space" | nearest of 198, 201, A17, A87, A66 | Levels 1, 2, A |
| "198", "room 198", "one ninety-eight" | 198 | Level 1 |
| "201" | 201 | Level 2 |
| "food" | 171, the Simpson atrium | Level 1 |
| "check-in" | 117, the JRR atrium | Level 1 |
| "bathroom", "water" | nearest marked one | after the on-site walk |
| something unknown | "I don't know that. Closest: ..." | |

Then the route is drawn on the venue map, across floors when needed, and spoken step by step.

## The venue map (from your screenshots, 2026-11-15)

Saved to `data/venue/` (git-ignored): `level_2.png`, `level_1.png`, `level_A.png`, `level_B.png`.
Two joined buildings: Julis Romo Rabinowitz (JRR, west, Washington Road side) and Louis A.
Simpson (LAS, east).

### Event rooms

| Room | Role on the map | Level | Building |
|---|---|---|---|
| 201 | Hacker space | 2 | JRR |
| 117 | Atrium, check-in | 1 (ground, entry) | JRR |
| 198 | Hacker space | 1 | JRR |
| 171 | Atrium, food | 1 | LAS |
| Café | Atrium Café | 1 | LAS |
| A01 | Sponsor room, CoStar Group | A (1 below ground) | JRR |
| A02 | Sponsor room | A | JRR |
| A12 | Sponsor room | A | JRR |
| A17 | Hacker space | A | JRR |
| A87 | Hacker space | A | JRR |
| A97 | Judges / mentors room | A | JRR |
| A98 | Judges / mentors room | A | JRR |
| A66 | Hacker space | A | LAS |
| A71 | Judging room | A | LAS |
| B60A, B60B, B60C | Hacker space | B (2 below ground) | LAS |

Other numbered rooms on the maps, with no event role: 101, 102, 144, 161 (Level 1);
200, 217, 254, 271, 298 (Level 2). They are in the catalog so a room number always resolves.

Level B is the fourth screenshot. It is recorded, but out of scope ("just the 3 floors").

### What the maps do not show

Bathrooms and water fountains are not marked. They still come from a walk with "Mark here".

## The gap that decides everything

**The Matterport scan covers only the JRR atrium and the corridors around it, on two levels.**

| Venue level | In the scan? | What is there |
|---|---|---|
| Level 2 | Yes: ring corridor around the void, room 217 hanging in the middle (29 scan points) | 201 is off this corridor |
| Level 1 | Yes: atrium 117 with the tables, ring corridor (36 scan points) | 198, 101, 102 are off this corridor |
| Level A | **No** | most event rooms: judges, judging, sponsors, three hacker spaces |
| Simpson side, all levels | **No** | 171 food, café, A66, A71 |

(Matterport labels these Floor 3, Floor 2 and a thin "Floor 1" slice with no scan points.
An earlier version of this plan called that three floors; the venue map shows it is two.)

So camera localization works in the atrium and its ring corridors only. The judges rooms are
one floor down, where the camera cannot be matched to anything.

### How to still get there

**The drawn path does not depend on the scan.** It is computed on the walk graph traced on the
venue plan, so it is shown in full from where you stand to the room's door, on every level
(the Level 1 part on the Level 1 plan, the Level A part on the Level A plan, joined at the
stairs). Only the moving "you are here" dot depends on tracking.

Three layers for the dot and the spoken guidance, each a fallback for the one above:

1. **Camera fix (exists).** In the atrium and ring corridors: position within about a metre.
2. **Carry the position past the scan with ARKit.** Once the atrium has given a fix, the phone's
   own motion tracking keeps the position going: out of the ring corridor, down the stairs
   (it tracks height), along the Level A corridor. Drift is about 1 to 2 % of the distance, so
   about 1 m after 60 m. This is phase 1 of `AR_VIEW.MD`. Not built yet.
3. **Spoken step list, no position needed.** The route as landmarks from the map: "Take the
   stairs down one level. At the bottom turn left. Second door on your right is A98." Works
   anywhere, and is what a person would be told at an information desk.

## Design

### 1. Venue catalog and search

`server/venue.json`: one entry per room.

```
{ id: "A97", number: "A97", level: "A", building: "JRR",
  roles: ["judges", "mentors"], name: "Judges / mentors room",
  aliases: ["judges room", "mentor room", "mentors"], x, y, door: [x, y] }
```

Search (`server/venue.py`, pure, tested), in this order:
1. **Room number.** Normalized: "a 97", "A-97", "room a97", "a ninety seven" all give A97.
   Spoken numbers are converted ("two oh one" is 201).
2. **Role or alias.** "judges", "judging", "sponsor", "costar", "hack", "food", "check in",
   "cafe", "bathroom", "water". "Judges room" and "judging room" are different rooms, so
   both are kept apart and the answer says which is which.
3. **Fuzzy** on names for typos ("juges room").

Several matches are ranked by walking distance from the person, same level first. The result
says the level: "A98, judges and mentors room, one level down, about 40 m".

Used by: a search box at the top of the map (like Google Maps), and two agent tools,
`find_place(query)` and `navigate_to(room)`. The agent gets the catalog's roles in its
prompt, so "where do I go for judging?" maps to the judging room without guessing.

### 2. The venue map becomes the app's map

The venue plan replaces the grey wall slices: it shows every room, on every level.

- Each screenshot is tied to our map coordinates by 3 or 4 matching points (atrium corners,
  ring corridor corners, the JRR / LAS divider) picked once, giving scale, rotation and shift
  per level. Level A is tied to Level 1 through features both share (atrium outline, divider,
  outer walls), since it has no scan.
- Check: the scan points drawn on the venue plan must all fall inside the atrium and ring
  corridors. If they do not, the picked points are wrong.
- The app shows the level you are on, with a level switch (2, 1, A). Event rooms keep the
  map's colours. The searched room is highlighted with a pin.

### 3. Walk graph on the venue map

The route graph today only has scan points. Rooms off the corridor and all of Level A need
more.

- `server/venue_graph.json`: corridor nodes (junctions, corners), door nodes (one per room),
  and links between levels (the atrium stairs, other stairs, the elevator), in map
  coordinates. Traced by clicking on the venue plan in a small helper page; about 20 minutes
  per level.
- Inside the scan, the existing scan point graph is joined to it at the ring corridor.
- A route is a list of legs with the level of each, drawn on the right level's map.
- The same route gives the spoken step list: turns, "door on your left / right", door counts
  along a corridor, "down one level".
- Stairs or elevator: elevator by default when there is one on the route, since the user is
  blind; "use the stairs" switches it.

### 4. Guidance

- In the scan: as today.
- Past the scan: ARKit carried position (layer 2), with the banner showing the tracking
  quality. When tracking is lost, the app switches to the step list and says so.
- On arrival: "A98 is the door on your right."

### 5. Bathrooms and water

"Mark here" in the app (stand at the door, press Mark, pick the kind) adds them to the same
catalog with `roles: ["restroom"]` or `["water"]`. From the access guide: restrooms are on
every level of JRR, with accessible unisex ones on Level A at the front and Level 1 towards
the rear. Exact doors need the walk.

## Steps

| # | Step | Gives | Size |
|---|---|---|---|
| 1 | Catalog from the three maps + search + API + tests | "judges room" and "A97" resolve | small |
| 2 | Search box in the app, results with level and role; agent tools | you can enter it | small |
| 3 | Tie the venue plans to map coordinates; venue plan as the app map, level switch, room highlight | you see where it is | medium |
| 4 | Walk graph traced on the plans, routing across levels, step list | a route and spoken steps to any room | medium |
| 5 | ARKit carried position past the scan | live guidance on Level A | medium to large |
| 6 | Mark here for bathrooms and water | "nearest bathroom" | small |
| 7 | On-site check of the "Done means" table | proof | 30 min |

Steps 1 to 4 reach the north star with live guidance in the atrium and a spoken step list
beyond it. Step 5 makes the guidance live all the way.

## Time

It is 05:07 on Sunday. Devpost closes submissions at 08:30, and judging runs 09:30 to 14:00.

- **Fits before 08:30:** steps 1 and 2 for certain; step 3 likely; step 4 for the handful of
  rooms in the demo (judges rooms, CoStar room, 198, 201) if the graph is traced only for
  those.
- **Does not fit:** step 5. It is native work with rebuilds and on-site tuning.

So the demo is: search "judges room", see the whole path drawn from the atrium to the A98
door (Level 1 plan to the stairs, then the Level A plan to the door), with your dot moving
live along it in the atrium. Past the stairs the path stays on screen and the dot stops at
the last known spot; the spoken steps cover that part.

## Still open

1. Which stairs and which elevator connect Level 1 to Level A, and where they come out. The
   maps show stairs in the atrium and in the Simpson atrium; this needs one look on site.
2. Bathroom and fountain doors.
3. "Perel atrium": not a name on the maps or on Princeton's pages. The scanned atrium is
   room 117, labelled "Atrium · Check-in".

## Sources

- HackPrinceton venue maps: your screenshots, now in `data/venue/`
- HackPrinceton Fall 2026: https://www.hackprinceton.com/
- Devpost listing and schedule: https://hackprinceton-fall-26.devpost.com/
- AccessAble guide, Julis Romo Rabinowitz: https://www.accessableusa.com/princeton-university/access-guides/julis-romo-rabinowitz
- Matterport API, model `P3S6VUTP3qB` (floors, rooms, floor plans in `data/matterport/floorplans/`)
