# Implementation plan: multi-pano localization, defurnished references, minimap

Written 2026-11-14. Status of everything below: planned, not built, unless marked done.

## Where things stand

- Phone app (Expo Go): streams 720px frames over the Tailscale funnel, portrait locked, speech plays through
  the silent switch. Not yet confirmed on a physical phone for localization quality.
- Server (Part 1) with real YOLO and the real localizer (Part 3). Done on `main`:
  - frames over 12 MP are downscaled instead of rejected (limit 50 MP, long side 1280)
  - viewer shows a live ranking of places with the received frame beside it, and a footprint box on the pano
  - people and furniture are masked out of the pano reference views (`server/refmask.py`, `MASK_REFS=1`)
- Measured on simulated frames from the same pano (100 positives, 100 negatives): true pano ranked first
  75 of 100 unmasked, 97 of 100 masked. Simulated data, so direction only.
- Known open problems:
  - results on the real phone were subpar (cause not yet measured, see "Diagnose first")
  - one false `LOCKED` in the 20 negative walkthroughs (target 0), so `make demo-check` fails
  - the phone app does not send `/api/speech` acknowledgments, so `make self-check` fails that one check
  - the viewer shows missing yaw as `0 deg`

## What we learned about the data

- Model `P3S6VUTP3qB` ("Princeton - Rabinowitz Hall") has 66 pano locations over at least two floors
  (z about 1.36 and about 5.34). Only one pano (`1k478kza39tbfu1uhbttg5ybb`, scan label 0) is a candidate place
  today. 21 neighbours are loaded as distractors.
- Model `R1FX6EmdcDt` has the same name and the same 66 scan positions. A side-by-side of the same scan point
  shows the same people and boxes, so it is a duplicate capture, not an unfurnished one. Ignore it.
- The unfurnished imagery is not an API model. The public viewer
  `https://my.matterport.com/models/6sd6A5r5EVp?section=downloads` offers 20 images named
  "Photo 1 Defurnished" to "Photo 20 Defurnished" plus a "Dollhouse View Defurnished" render.
  - The API returns `not.found` for `6sd6A5r5EVp` with our key (see `INTEGRATION.md`), so these must be
    downloaded by hand from that Downloads page and placed in `data/defurnished/`.
  - They are ordinary perspective photos: no cube faces, no known pose, unknown field of view.
  - They show the empty structure (stairs, columns, glass, wood ceilings) without furniture or people.
- Face order of the skybox (0 up, 1 to 4 around, 5 down) is verified for the demo pano by Part 3's seam check.

## Diagnose first (cheap, do before the big build)

1. Restart the server so masking is active (done 2026-11-14; `/api/pano` reports 466 masked regions).
2. Estimate the phone camera field of view: take a few photos at the scan point and run `make p3-hfov DIR=...`.
   The localizer assumes 55 degrees (`HFOV_DEG`); a 4:3 iPhone photo is wider, and a wrong value distorts
   every match.
3. Record one real test walk with `RECORD=1`, replay it, and list which frames fail and why (retrieval miss,
   too few inliers, competitor too close, yaw jump).
4. Confirm the phone stands within about a metre of a scan point. Reference views come from one viewpoint.

Acceptance: we can name the main cause of the subpar results with numbers from a recorded walk.

## Task 1: download all 66 panos

- Fetch the six 2k skybox faces for every pano location of `P3S6VUTP3qB` into `data/pano/<id>/face_0..5.jpg`
  (git-ignored). About 3 MB each, roughly 200 MB total. Use Part 3's `scripts/fetch_matterport.py` or its
  `make p3-fetch` with the pano list, not ad-hoc scripts, so layout detection runs for every pano.
- Run `make p3-refs` for all of them: seam-continuity layout check plus 72 reference views per pano.
- Credentials are in `.env.local` as `MATTERPORT_API_KEY` and `MATTERPORT_API_TOKEN`. Never print them.
  Do not export empty variables of the same names (an empty env var overrides the file).

Acceptance: 66 directories with six faces, a verified `layout.json`, and 72 reference views each.

## Task 2: localize against all panos (which scan point are we at?)

Goal: report the pano the user is nearest to, with a ranking, instead of treating one pano as the answer and
the rest as distractors.

Design:
- Promote the panos to candidate places (`demo=True`) instead of distractors. Start with the same floor and
  the nearest K by distance, then all 66 once speed allows.
- Retrieval ranks places by the best matching reference view (already exposed as `match.ranking`).
- Geometric verification runs only on the top few candidate views (as today) and picks the place with the
  most inliers.
- Require a margin over the runner-up place before reporting `LOCKED` for a new place. Switching place resets
  the yaw-consistency state machine.
- `Location.pano_id` is the chosen place; `yaw_deg` comes from the match against that place.
- Mask people and furniture in every place's reference views (already built; cached per place).

Costs and risks:
- About 66 x 72 = 4,750 reference views. The first build masks, embeds, and extracts features for all of them.
  Expect many minutes once, then cached under `data/pano/<id>/cache/`.
- This changes Part 3's decision logic (`FrameEval.decide`, `LocStateMachine`, `PLACE_RATIO`), which assumes
  one demo place and competitors. Keep the single-pano mode working behind a flag.
- Neighbouring scan points look alike in a repetitive hallway; the margin rule and the compass heading are the
  main defences.

Tests first:
- unit: place choice with a margin; no switch without margin; place change resets yaw state
- procedural room (Part 3 `p3_room.py`): locks to the right place among several demo places
- regression: single-pano mode behaves as before

Acceptance: on simulated frames from a random pano, the chosen place is correct at a target rate we agree
beforehand, with zero false `LOCKED` in negative walkthroughs.

## Task 3: defurnished references

The 20 defurnished photos are extra references for the same locations, alongside the furnished pano views
(the live scene is furnished, so keep both, do not replace).

Steps:
1. Download the 20 photos and the dollhouse render by hand from the Downloads page into `data/defurnished/`
   (git-ignored; check Matterport's data terms before publishing anything).
2. For each photo, find its location: match it against the pano reference views with the existing geometric
   verification to get (place, yaw, pitch) and an estimated field of view. Photos that do not verify are
   dropped and logged.
3. Add the verified photos as extra reference views for that place, tagged `source="defurnished"`.
4. Apply masking and contrast normalization the same way as other references.

Risks: unknown field of view and possible non-rectilinear projection; the pose estimate may be wrong, so a
photo must pass the same inlier test as a LOCKED frame before it is used.

Acceptance: report how many of the 20 photos were placed, and the change in ranking accuracy with and without
them on the evaluation set.

## Task 4: minimap

Goal: a top view that shows where the localizer thinks we are.

Data we have:
- every pano has `position {x, y, z}` in meters (`data/matterport/panoLocations.json`)
- Part 3 already wrote a 900 x 900 top-down image at `data/matterport/topdown.png`
- the "Dollhouse View Defurnished" render is a 3D view, not an orthographic plan, so it is a backdrop at best

Build:
- Server: expose all pano positions and floors in `/api/pano` (id, label, room, x, y, z).
- Viewer: draw all scan points as small dots, one map per floor (split by z), highlight the chosen place,
  show the top-ranked alternatives in a lighter colour, and keep a short trail of recent places.
- Heading arrow: needs the relation between a pano's yaw 0 and the map axes. Not verified. Calibrate once by
  standing at a known place facing a known direction, then store the offset in config. Ship the dot first.

Tests: pure functions for floor split, coordinate scaling, and nearest-place lookup; a headless-Chrome check
that the highlighted dot moves when the canned place changes.

Acceptance: the highlighted dot follows the ranked place in the viewer on the simulated feed.

## Task 5: loose ends from earlier

- Phone app: send `POST /api/speech` after "Insight ready" and for each spoken cue with the capture to
  speech-start time (see `NOTES-part1.md`). This makes `make self-check` pass and fills the latency metric.
- Viewer: show a missing `yaw_deg` as unknown, not `0 deg`. Show 409 warm-up and capture errors.
- Ranking order: sort by verified inliers first, then retrieval similarity (retrieval alone is noisy).
- Part 3 evaluation: find the one false `LOCKED` negative walkthrough and fix it, then re-run `make p3-eval`.
- Make the localizer, `/api/pano` and the viewer agree on one source of place names (`pano.json` today).

## Order of work

1. Diagnose first (hfov, one recorded walk).
2. Task 1 (download all panos) in the background.
3. Task 2 (multi-place), then re-run the evaluation and compare against the single-pano baseline.
4. Task 4 (minimap) once the chosen place is trustworthy.
5. Task 3 (defurnished references) after the user downloads the photos.
6. Task 5 loose ends.

## Not in scope

- Routing between panos or turn-by-turn guidance (slides only in the project brief).
- Replacing the furnished references with unfurnished ones.
- Anything that claims accuracy beyond what is measured and recorded in the notes.
