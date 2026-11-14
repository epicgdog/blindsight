# NOTES-part3: Matterport and localization

Branch `part3-loc`. Commits are prefixed `part3:`.

## Status, 2026-11-14

| Item | State |
|---|---|
| `.env*` git-ignored before the first commit | done (`cb2bb95`) |
| A1: Matterport credentials in `.env.local` | **missing, blocked**. The doc says stop here, so Phase 3 has not started against real data |
| Matterport quickstart | **unreadable from the build container**: `betaapp.matterport.com`, `beta-app.matterport.com`, `my.matterport.com` and `api.matterport.com` all return 403 from the network policy |
| Code: fetch, refs, localizer, geometry, synthetic data/feed, calibrate, eval, tests | done; 41 tests pass (`make -f make/part3.mk p3-test`) |
| Phase 3 acceptance (6 faces, contact sheet viewed, GET /api/pano) | **not met**: no pano data |
| Phase 4 targets on the lobby (simulated set) | **not measured**: no pano data. No lobby number exists yet; the pitch must not quote any |
| Pipeline validated end to end | yes, on a procedural test room: all Phase 4 targets pass there in the deployed config, stress test fails (see below); not the lobby |

### What unblocks Part 3 (A1, about 2 minutes, plus one paste)

1. Create `.env.local` in the repo root with the credential variables the quickstart names (it is git-ignored).
2. Fill `data/matterport/api.json` from the quickstart, using `data/matterport/api.example.json` as the template (endpoint, auth scheme, env var names, the panoLocations query, where face URLs live). Or paste the quickstart text to the Part 3 agent so it can fill it. Nothing there is guessed, as the doc requires.
3. On the demo laptop, on good internet:

```bash
make -f make/part3.mk p3-weights
```

```bash
make -f make/part3.mk p3-fetch
```

Then look at `data/matterport/topdown.png`, `data/pano/<PANO_ID>/contact_cross.jpg` and `horizon.jpg` (text must read correctly, faces upright and continuous), and run:

```bash
make -f make/part3.mk p3-all
```

That builds the reference views, the simulated set, the calibration, and writes the held-out results into the "Phase 4 results" section at the end of this file.

## How the localizer decides (as built)

1. Mask movable classes (`geometry.MOVABLE_CLASSES`: person, chair, couch, bench, bags, bottles, laptops, tv, ...): boxes are blanked before embedding, keypoints inside them are suppressed and dropped before matching.
2. Proposals: global retriever top 3 (DINOv2-small, then open_clip ViT-B-32, then a weight-free HOG descriptor), plus 2 from a bag of visual words over the frame's own local features, plus the view nearest the gyro-predicted yaw while tracking.
3. Geometric verification per candidate: SuperPoint+LightGlue (fallback SIFT, ratio 0.75), RANSAC homography (5 px), then keep only inliers that a pure rotation explains within 3 degrees. Matched when inliers >= N_MIN and they cover >= GRID_CELLS_MIN of 9 cells.
4. Competing places: the views of the other downloaded scan points (up to 20) and any frames captured with Part 1's `POST /api/capture/neg` (`data/negatives/`) are verified too (the vocabulary picks the 3 best). The locked pano must have at least PLACE_RATIO times the best competitor's inliers. Only the chosen pano can ever be LOCKED; this is a precision check, not multi-pano routing.
5. Heading: rotation (Kabsch on bearing vectors) between the live frame and the best reference, composed with the reference pose.
6. State machine: LOCKED when 2 of the last 3 frames match with yaw agreeing within 15 degrees (heading-compensated when the phone sends `heading_deg`), LOST after 5 consecutive failures, yaw carried by the compass between fixes, a visual jump over 40 degrees needs a second agreeing frame.
7. `add_reference` (warm-up): the same test against the pano-derived views only, so on-site references cannot bootstrap off each other; capped at 150, kept in memory.

`score` is the global retriever's cosine similarity for the best verified candidate. `T_LO` gates retriever proposals, `T_HI` is the retrieval-only threshold for the ablation. Neither decides LOCKED.

## Config block for config.py

Part 1 owns `server/config.py`; this is the Part 3 block to paste between the markers (requested in HANDOFF.md). Until it lands, `localize.Settings.load()` reads the same values from env and `data/calibration.json` itself.

```python
# --- part 3 ---
import json as _p3_json
import os as _p3_os
from pathlib import Path as _P3Path
_p3_cal_file = _P3Path(__file__).resolve().parents[1] / "data" / "calibration.json"
_p3_cal = _p3_json.loads(_p3_cal_file.read_text()) if _p3_cal_file.exists() else {}
T_HI = float(_p3_os.environ.get("T_HI", _p3_cal.get("T_HI", 1.01)))
T_LO = float(_p3_os.environ.get("T_LO", _p3_cal.get("T_LO", -1.01)))
N_MIN = int(_p3_os.environ.get("N_MIN", _p3_cal.get("N_MIN", 30)))
PLACE_RATIO = float(_p3_os.environ.get("PLACE_RATIO", _p3_cal.get("PLACE_RATIO", 1.5)))
GRID_CELLS_MIN = int(_p3_os.environ.get("GRID_CELLS_MIN", 3))
YAW_AGREE_DEG = float(_p3_os.environ.get("YAW_AGREE_DEG", 15))
YAW_JUMP_DEG = float(_p3_os.environ.get("YAW_JUMP_DEG", 40))
LOCK_NEED = int(_p3_os.environ.get("LOCK_NEED", _p3_cal.get("LOCK_NEED", 2)))
# --- part 3 ---
```

## Assumptions

1. Branch name: the doc says `part3-loc`; the request was "a new branch called part 3". Used `part3-loc` so Part 1's merge flow finds it.
2. Layout: `server/` is the uv project root (Part 1 doc), so `localize.py`, `geometry.py` and `refs.py` live in `server/`, tools in `tools/`, tests in `server/tests/test_p3_*.py`.
3. `protocol.py` and `config.py` are not on main yet. `localize.py` imports `Detection` and `Location` from `protocol` and falls back to identical dataclasses until it exists; it reads `config.py` when present, else env, else `data/calibration.json`, else safe defaults.
4. Matterport API: only `id`, `label` and `position` of panoLocations are taken as known (from PLAN.md); everything else comes from `data/matterport/api.json`.
5. Pano frame: yaw clockwise seen from above, 0 at the centre of horizon face 1 after layout verification. The vertical axis of positions is the axis with the least spread across scan points (one floor).
6. Face order is checked, not assumed: `refs.detect_layout` scores colour continuity over all 12 cube edges for clockwise and counter-clockwise horizon order and every up/down rotation, and flags upside-down horizon faces. Tested by permuting and flipping faces. Mirrored faces cannot be detected from seams (a mirrored cube is self-consistent), so the contact sheet must be read for backwards text; `layout.json` has a manual `mirrored` flag.
7. Reference views: 360x480 (portrait 3:4, the iPhone photo and preview aspect) at HFOV_DEG=55. Live frames are resized to width 360; their own aspect is kept and intrinsics follow it.
8. `Location.pano_id` and `label` are always the chosen pano (single-pano system); `state` is the only lock signal.
9. Competing places: other scan points of the same model are verified as competing hypotheses. Reason, measured on the test room: views of the same walls from scan points 2.5 to 5 m away kept 47 to 388 rotation-consistent inliers against N_MIN 30, and the share of homography inliers a rotation explains did not separate them either (19 of 189 hard negatives at 95% or more).
10. Synthetic positives use uniform random yaw over 360 degrees (harder than reference yaw plus jitter, because live frames do not line up with the 15 degree grid), pitch within 12 degrees, roll sd 2 degrees, 2% scale, 2% translation (ground truth corrected for it), and photometric augmentation.
11. Hard negatives: at least 60, weighted toward the nearest scan points; `fetch_matterport.py` downloads the 20 nearest by default.
12. Stress test: every third other scan point by distance, starting with the second nearest, is held out of the competing places and its frames are evaluated separately.
13. Calibration margin: zero calib false matches even at N_MIN/1.2 and PLACE_RATIO/1.15; among those, best calib recall, then the smallest thresholds (real phone frames will have fewer inliers than renders, so the smallest safe values protect recall).
14. Time to LOCKED in eval: a 10 fps stream with latest-frame-wins, so each processed frame costs max(0.1 s, processing time).
15. Matcher and retriever are recorded in `calibration.json`; the Localizer uses those, because thresholds are matcher-specific.

## Fallbacks taken (doc order)

1. `lightglue` installs and runs here (weights come from GitHub releases), but on this 4-vCPU cloud container, CPU only, SuperPoint took about 260 ms per frame and LightGlue 130 to 200 ms per pair, about 1.7 s per update: it fails the 150 ms per candidate target here. SIFT measured about 35 ms per frame and about 12 to 25 ms per candidate, so the test-room runs use `--matcher sift`. On the demo MacBook (mps), re-run calibrate and eval with `--matcher lightglue` and keep whichever passes latency and precision.
2. `mps`: not present on this Linux container; the code falls back from mps to cpu automatically.
3. DINOv2: `huggingface.co` is blocked here, open_clip is not installed, so retrieval fell back to the weight-free HOG descriptor. Retrieval only proposes; the bag-of-words proposals cover weak retrieval. Run `make -f make/part3.mk p3-weights` on the demo laptop.
4. SIFT matching uses exact brute-force kNN with the 0.75 ratio test instead of FLANN: measured 8.6 ms versus 17.8 ms for about 1000x1000 descriptors, and deterministic (FLANN's randomized kd-trees changed a yaw median by 5e-4 degrees between two runs in one process, which broke exact reproducibility). `--matcher sift-flann` restores FLANN.
5. Matterport API and credentials: missing and blocked. The Playwright screenshot fallback cannot run here either (`my.matterport.com` is blocked). The manual fallback is implemented: `python scripts/fetch_matterport.py --import-faces DIR --pano-id ID` (four horizon screenshots work; missing up and down faces become neutral grey and must be logged here).

## Measurements on the procedural test room (NOT the lobby, NOT for the pitch)

`server/tests/p3_room.py` ray-casts a 16 x 12 x 4 m textured box from 7 scan points (demo point plus six 2.5 to 7.2 m away). Flat walls viewed from another scan point are a worst case for the rotation check. These numbers validate the pipeline only.

<!-- room-smoke:begin -->
Pipeline: `refs.py --all`, `make_synthetic.py` (400 positives, 400 hard negatives from the 6 other scan points, 20 + 20 walkthroughs), `calibrate.py --matcher sift` (calib split), `eval_localization.py` (held-out split). Retriever: the weight-free HOG fallback (DINOv2 weights unreachable here). Machine: 4-vCPU cloud container, CPU only.

Calibration (calib split, 300 + 300 frames): N_MIN 30, PLACE_RATIO 1.0, T_LO 0.869, T_HI 0.979; calib recall 100%, 0/300 false matches with the safety margin; retrieval alone 3/300 (1%, by construction of T_HI).

Run 2026-11-14T08:33:35+00:00, model `procedural-test-room`, pano `room_p0` (test room). **SIMULATED data, held-out test split.**
Config: matcher `sift`, retriever `classic-hog`, N_MIN 30, PLACE_RATIO 1.0, T_LO 0.8692, T_HI 0.9786, LOCK_NEED 2, HFOV 55.0, competing places 6.

| Metric | Target | Measured (test split) | Pass |
|---|---|---|---|
| LOCKED precision on held-out negatives: per-frame false-match rate | < 1% | 0.00% | PASS |
| Recall: held-out positives matched | >= 90% | 100.00% | PASS |
| Yaw error median | < 8 deg | 0.04 deg | PASS |
| Yaw error p95 | < 20 deg | 0.11 deg | PASS |
| Latency per candidate (verify + share of extraction, p95) | < 150 ms | 30 ms | PASS |
| False LOCKED in negative walkthroughs | 0 of 20 | 0 of 20 | PASS |
| LOCKED within 2 s | 20 of 20 | 20 of 20 | PASS |
| Overall localization rate (update(), this machine) | >= 2 Hz | 6.0 Hz | PASS |

Per frame: 100/100 positives matched, 0/100 negatives falsely matched (precision 100.00%).
Ablation, per frame false-match rate: retrieval alone 2.00% (2/100) vs with geometry 0.00% (0/100).
Ablation, negative walkthroughs with a false LOCKED: retrieval alone 0 of 20 vs with geometry 0 of 20.
Negatives by distance from the locked scan point: 2-4 m: 0/47 false, 4-8 m: 0/53 false.
Stress (held-out scan points room_p2, room_p5 removed from competing places): 11/31 frames falsely matched (35.48%), 4 of 7 walkthroughs falsely LOCKED.
Latency p50: extract 35 ms, embed 1 ms, verify 18 ms per candidate, 7.3 candidates per frame in raw evaluation (runtime verifies competitors only when needed).

Time to LOCKED in the 20 positive trials: 0.25 to 0.60 s (second or third frame).

**Stress finding (important for the lobby).** In the deployed configuration every other scan point is a competing place and nothing falsely locks. When a scan point is left out of the competing places (stress test), 35% of its frames falsely match and 4 of 7 walkthroughs falsely LOCK. So precision depends on competing views covering everywhere the user might stand. Actions taken: `fetch_matterport.py` now downloads the 20 nearest scan points and the Localizer uses up to 20 (per-frame cost is unchanged, the vocabulary still picks 3 views); frames saved by Part 1's `POST /api/capture/neg` into `data/negatives/` are loaded as competing views. On the room, a scan point outside the competing set falsely locked 3 and 5 of 6 frames in two pans; with 18 captured frames from it as on-site negatives, 0 (test `test_onsite_negative_frames_become_competing_views`). Recommendation for A3: capture a few negatives (`/api/capture/neg`) at spots between scan points around the demo spot, and run the eval after `p3-fetch` to see the stress number for the real lobby.

Other measurements on the room:
- Yaw from rotation: under 0.1 degree error on renders; the geometry tests assert under 3 degrees.
- Competing-view proposals for hard negatives: global HOG top 3 found the frame's own scan point for 270/300; vocabulary top 3 with 256 words found the right view 144/150, with 1024 words 150/150 (1024 kept).
- Latency, SIFT: extract 35 ms, verify 18 ms per candidate (p50), update() 167 ms mean (6.0 Hz). SuperPoint+LightGlue on this CPU: about 1.7 s per update.
<!-- room-smoke:end -->

## Honest limits

- One pano gives the scan point and the heading, not a metric position. Nothing finer is claimed.
- Simulated positives are rendered from the pano itself: no parallax from standing off the scan point, no lens distortion, no real lighting or people. Real-phone accuracy is unverified until A3; the warm-up (`add_reference`) reduces that risk and its accept count should be logged here.
- The procedural room has planar walls and synthetic textures; the lobby has depth (which helps the rotation check) but also glass, reflections and repeated patterns (which hurt).
