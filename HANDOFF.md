# HANDOFF

One line per request: who, file, exact change.

- Part 3 -> Part 1, `server/config.py`: paste the Part 3 block from NOTES-part3.md ("Config block for config.py") verbatim between its `# --- part 3 ---` markers; it loads T_HI, T_LO, N_MIN, PLACE_RATIO from data/calibration.json and adds PLACE_RATIO and LOCK_NEED (both Part 3 variables).
- Part 3 -> Part 1, `server/pyproject.toml`: add `lightglue @ git+https://github.com/cvg/LightGlue.git` (SuperPoint+LightGlue; OpenCV already arrives with ultralytics, numpy/torch/transformers are in your base list).
- Part 3 -> Part 1, `Makefile`: `include make/part3.mk`, and make `make check` run `p3-check` (Part 3 tests, about 90 s).
- Part 3 -> Part 1, `server/server.py`: with LOCALIZER=real build `localize.Localizer()` once at startup (it raises FileNotFoundError with the fix if pano data is missing), call `update(frame_rgb, detections, heading_deg)` per frame, use `localizer.last_debug["embed"]` for `server_ms.embed`, and have the warm-up endpoint call `add_reference(frame_rgb)` per frame.
- Part 3 -> Part 1, `server/server.py` GET /api/pano: return `refs.pano_info(thresholds=localizer.s.thresholds())`; face and ref paths in it are relative to `data/`, so mount `data/` as static files.
- Part 3 -> Part 1, `tools/sim_client.py`: in image-directory mode read `heading_deg` per frame from `frames.jsonl` when present (written by `tools/synthetic_feed.py`), or import `synthetic_feed.iter_feed()` directly.
- Part 3 -> Part 1, self-check: run `python server/localize.py --selfcheck` (exit 0 = PASS; prints a PASS/FAIL line for "localizer loaded" (pano, refs, models) and for "calibration loaded").
- Part 3 -> Part 1, `make demo-check`: run `tools/eval_localization.py --no-stress` and fail on a non-zero exit (it exits 1 when any Phase 4 target fails).
- Part 3 -> Part 1, `server/config.py` HFOV_DEG: keep 55 until measured; `python tools/calibrate.py --estimate-hfov data/onsite/pos` estimates it from warm-up frames, and Part 3 will request the measured value here.
- Part 3 -> Part 2, `server/viewer/`: `location.best_ref` is a reference view name; match it to `refs[].name` from GET /api/pano for the thumbnail to highlight (warm-up references are named `onsite###_y###` and have no thumbnail).
- Part 3 -> Part 2, `tools/render_backup_video.py`: `python tools/synthetic_feed.py --video <out.mp4> --seconds 36` renders a full turn at the locked pano.
- Part 3 -> Part 1, `server/server.py` POST /api/capture/neg: after saving the frame to `data/negatives/`, also call `localizer.add_negative(frame_rgb)`; those frames become competing views at once (and are loaded from `data/negatives/` at every startup). Measured reason in NOTES-part3.md, "Stress finding".
