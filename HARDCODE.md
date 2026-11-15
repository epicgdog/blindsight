# HARDCODE plan: one floor, text and objects to tell look-alike sides apart

Status: plan only, nothing implemented. Written 2026-11-14.
Order: Step 0 -> Step 1 -> Step 2 -> (later) LoMa matcher A/B.

## Why

- The scan has 3 height levels (`server/minimap.py` split by `FLOOR_GAP_M`):
  - floor 0: z 1.37, 36 panos, has `walls_floor1.json` (ground floor, KEEP)
  - floor 1: z 3.36, 1 pano (probably a stair landing, DROP)
  - floor 2: z 5.37, 29 panos (upper floor, DROP)
- Some places have the same structure on both sides and differ only by text (signs, room
  numbers, posters). DINOv2-small retrieval cannot separate them: at ~224 px a sign is a few
  pixels, and the pooled CLS + mean-patch embedding encodes layout and surfaces, not text.
  Near-identical embeddings for both sides. DINO is also not a reliable "is text present" test;
  an OCR text detector answers that directly and cheaply.

## Step 0: merge with origin/main first

`origin/main` has 3 teammate commits (d8471e5, a2d8ad7, ba3f276) touching `localize.py`,
`server.py`, `App.js`, `viewer/app.js`, new `property_localize.py`. The changes below touch the
same files, so resolve once, first.

1. Stash the uncommitted viewer 3D-map work (not ours: `server/viewer/*`, `map3d.js`, `vendor/`).
2. Rebase `main` onto `origin/main`, resolve conflicts.
3. Check whether `property_localize.py` chooses candidate places on its own (filter must cover it).
4. Run all tests (server 259, app 68, viewer 34), unstash.

## Step 1: ground floor only

Filter, do not delete `data/pano/*`, so it is reversible with an env var.

1. Config: `FLOOR` in `server/config.py` (default `0`; `all` disables the filter).
2. Helper in `server/minimap.py`: `panos_on_floor(data_dir, floor) -> set[str]`, reusing the
   existing height-gap split. Single source of truth for "which panos are on this floor".
3. Localizer (`server/localize.py`):
   - `_candidate_ids`: keep floor-0 ids only (36 places loaded instead of 66).
   - `_competitor_ids`: drop negatives on other floors.
   - If the configured `pano_id` is not on floor 0: fail at startup with a clear message.
   - `_scan_xyz` (tracker scan points): floor-0 points only.
4. Property localizer (teammate's `property_localize.py`): same filter where it lists places.
5. Minimap (`scan_points`): only floor-0 points, `floors` with one entry, bounds of that floor.
   Viewer "another floor" paths then never fire.
6. Position (optional): crop the mesh to floor 0's z range before ray casts for faster load and
   casts. Only if load time is a problem. `pano_frame.json` stays valid (one global transform).
7. Tools: apply the filter in `tools/make_synthetic.py` (hard negatives),
   `tools/eval_pitch_gate.py`, and `make p3-calibrate`. Re-run `make p3-calibrate` afterwards
   (thresholds depend on the candidate set).
8. Tests:
   - 3-floor fixture: only floor-0 ids reach candidates, minimap and tracker.
   - `FLOOR=all` reproduces current behaviour.
   - `pano_id` on another floor raises at startup.
   - Update `test_minimap` floor-split tests to test the helper, not the output.
9. README: document `FLOOR`.

Expected side effects: faster startup, no false matches to look-alike rooms upstairs.

## Step 2: OCR + YOLO tie-breaker for look-alike sides

YOLO already exists: `server/detect.py` (yolo11n, obstacles) and `MASK_REFS` (masks people and
furniture out of reference views). New use: its object classes (fire extinguisher, exit sign,
plant, ...) as a weak per-side signature next to text.

### 2a. OCR engine

- First choice: Apple Vision through `ocrmac` (server is a Mac; accurate, fast, Neural Engine).
- Fallback: RapidOCR (`rapidocr-onnxruntime`, PaddleOCR models, small, cross-platform).
- Skip EasyOCR (torch, slower).
- Pick by running both on the real pano reference views and comparing what they read.
- Install with `uv add --project server <pkg>`.

### 2b. Offline: label every floor-0 place

New tool `tools/build_place_text.py`:

1. OCR each place's reference views (all yaws). Store per place in `data/pano/<id>/ocr.json`:
   text, box, bearing (yaw/pitch of the box centre), confidence.
2. YOLO on the same views: class counts per bearing, same file.
3. Normalise text: uppercase, collapse whitespace, fix O/0 and I/1 in numeric contexts, drop
   single characters. Numbers and room codes weigh most.
4. Manual labels: `data/matterport/place_labels.json`, e.g.
   `{"pano": "<id>", "yaw": 90, "text": "RM 104"}`. Overrides OCR; covers signs too small to
   read in the pano. User supplies these (can stand facing the right side to confirm).
5. Look-alike finder: list place pairs with near-identical DINO embeddings or high cross-match
   inliers, with the text each side has. User confirms which pairs are the real problem.

### 2c. Online: read text only when unsure

1. Trigger OCR on the phone frame only when the localizer is unsure: top-2 candidate margin
   below a threshold, or the top-2 are a known look-alike pair. Keeps cost off the 2 Hz loop.
2. Fuzzy-match frame text against each candidate's text near the matched bearing:
   - match: strong bonus
   - contradiction (different room number at that bearing): penalty
   - no text read: no evidence (OCR misses often; absence never decides)
3. YOLO class overlap: small bonus, tie-break only.
4. Debug payload: `match.text = {seen, expected, verdict}`; show it in the viewer.
5. Optional speech: announce a read sign ("Room 104 ahead"). Useful for a blind user, already
   computed.

### 2d. Evaluation

1. User stands on each side of each look-alike pair, facing the right side, ~10 photos per side.
2. A/B right-place rate with text step on vs off. Keep only if it wins.
3. Tune bonus / penalty / trigger margin on those photos.

### 2e. Tests

- Synthetic `server/tests/p3_room.py`: two identical rooms differing only by a sign texture.
  Text off -> tie; text on -> correct room wins.
- Unit tests: normalisation, fuzzy matching, contradiction vs no-evidence.

## Later (after this plan)

1. On-site test on floor 0: pitch sign, walking/still thresholds, frame rate, position quality,
   real-photo HFOV (`make p3-hfov`).
2. LoMa matcher A/B (`matcher=loma`, B or B128) on ~50 floor-0 phone photos vs
   SuperPoint+LightGlue. Switch only if it wins, then recalibrate.
3. Draw `match.position` on the minimap.
4. "Entrance" label: needs a definition from the user.

## Open questions for the user

1. Is floor 0 (ground, 36 panos) the floor to keep?
2. OK to edit the viewer files that hold someone else's uncommitted 3D-map work?
3. Env-var filter (recommended) or physically delete the other-floor pano folders?
4. Which spots look alike? Or run the look-alike finder first and review its list.
5. Small signs that matter (room numbers, door plates, posters) that need manual labels?
6. OCR on the server (recommended) or on the phone (iOS Vision, needs a native Expo module)?
