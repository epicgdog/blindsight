# Integration handoff

- Part 2, `app/src/speech.js` / `app/App.js`: send actual playback acknowledgments to `POST /api/speech`; measure capture-to-speech-start on the phone's monotonic clock and include the emitted cue ID (see NOTES-part1.md). Current app logs startup speech but does not provide server telemetry.
- Part 2, `server/viewer/app.js`: render null `yaw_deg` as unknown rather than `Math.round(null) === 0`; the stub intentionally reports no heading. Handle 409 warm-up/capture responses visibly. The core already matches `jpeg_b64`, `p50_ms`/`p95_ms`, and `running/seconds/elapsed/kept` used on branch `part_2`.
- Part 3, `server/localize.py`: import shared types from `server.protocol`; export no-argument `Localizer`, `update(rgb, detections, heading_deg) -> Location`, and `add_reference(rgb) -> bool`. Updates arrive for every processed frame; implement the EMBED_EVERY_N_FRAMES cadence internally. Do not use stub state as localization evidence.
- Part 3 synthetic feed integration is complete: Part 1 consumes native `iter_feed()` / `FeedFrame` and preserves `frames.jsonl` compass headings. No Part 3 source changes are required.
- Part 3 metadata/evaluation integration is complete: native `pano/pano.json`, `refs/refs.json`, calibration and `eval/results.json` are supported. `make demo-check` runs the native evaluator before streaming and rejects failed/missing targets. Real lobby assets and accuracy evidence remain required.
- Integrator, root `pyproject.toml` / `uv.lock`: Part 2 independently introduced a root project. Preserve Part 1's uv workspace and server member when merging; union Part 2's qrcode/runtime and python-pptx/pytest-timeout dev requirements, include both test directories, then regenerate uv.lock with `uv lock`. Do not choose one side wholesale.
- Integrator, `Makefile`: Part 1 includes `make/part2.mk` and `make/part3.mk` when present. Run `make check`, `make part2-test`, Part 3's offline evaluation, then the full `make self-check` and `make demo-check` before merging into main. Part 1 branch name is `part-1` as requested by its owner.

- Part 3 startup/cache integration is complete: `reset()` reuses loaded models and accepted warm-up references, `add_negative()` updates competing views immediately, and `last_debug["embed"]` supplies embedding timing. LightGlue is pinned and installed. The Part 3 config block remains yours; its current Settings.load() fallback already reads calibration/env correctly.


---

# Part 2 handoff

One line per request: **who, file, exact change.** Add yours below; keep working meanwhile.

## From Part 2 (phone, viewer, pitch)

### To Part 1 (core server)

- Part 1, `Makefile`: add the line `include make/part2.mk` (targets: `part2-echo`, `part2-tunnel`, `part2-test`, `part2-app-test`, `part2-video`, `part2-slides`).
- Part 1, `server/server.py`: serve the viewer. `GET /` returns `server/viewer/index.html`, and mount `server/viewer/` as static files at `/viewer` (the viewer uses absolute `/viewer/app.js`, `/viewer/logic.js`, `/viewer/style.css`). `.js` must be served as a JavaScript MIME type because the viewer uses ES modules.
- Part 1, `server/server.py`: add `GET /api/capture/auto` returning `{"running": bool, "elapsed": float, "seconds": int, "kept": int}`. The viewer polls it every 500 ms for warm-up progress. The contract only defines the `POST`, which should return `{"seconds": int, "kept": int, "running": true}` immediately and run the 20 s warm-up in the background.
- Part 1, `server/server.py`: `/metrics` must return the keys `p50_ms`, `p95_ms` and `fps` (the viewer reads exactly these).
- Part 1, `server/server.py`: `/ws/viewer` messages are the 5.2 JSON plus `jpeg_b64` (base64 of the latest frame), throttled to 10 fps. The viewer ignores messages without it only for the image; everything else still renders.
- Part 1, `server/cues.py`: keep the location cue's `id` stable across repeats (for example `loc-main-lobby`). The phone dedupes on `id`, so a new id re-speaks it. Set `interrupt: true` on the location cue and on any urgent obstacle cue.
- Part 1, `server/server.py`: `GET /api/pano` must return `faces` as six URLs the server actually serves (for example `/pano/face_0.jpg` to `/pano/face_5.jpg` from `data/pano/<PANO_ID>/`). The viewer's reference strip is blank without them.
- Part 1, `server/server.py`: apply EXIF transpose on decode if the frame is not upright; Part 2's app sends unprocessed portrait captures.
- Part 1, `pyproject.toml`, `uv.lock`: Part 2 created both at the repo root. Merge with yours. Part 2 added runtime deps `fastapi`, `uvicorn[standard]`, `websockets`, `pillow`, `qrcode` and dev deps `pytest`, `httpx`, `pytest-timeout`, `python-pptx`. Remove `qrcode` or `python-pptx` only if you also drop `scripts/tunnel.sh` or `slides/build_deck.py`.
- Part 1, `.gitignore`: Part 2 appended `.venv/`, `__pycache__/`, `node_modules/`, `data/`, `*.pyc`, `.expo/`, `dist/`, `slides/backup.mp4`. Keep them when merging.
- Part 1, `tests/`: Part 2 added `tests/__init__.py` and `tests/part2/`. If you add `tests/conftest.py`, keep fixture names unique (Part 2 uses `server`, `jpeg_bytes`).
- Part 1, switching from the stand-in: no code change. Point the phone at the real server URL, then run `uv run pytest tests/part2`. The stand-in fixture starts `app/dev/echo_server.py`; to test the real server, change the `server` fixture in `tests/part2/conftest.py` to launch it.

### To Part 3 (Matterport and localization)

- Part 3, `tools/synthetic_feed.py`: expose `frames(n: int)` yielding `(jpeg_bytes, width, height)` tuples, one per frame, panning across the pano. `tools/render_backup_video.py` uses it automatically if the file exists and has `frames`; otherwise it uses a placeholder pano.
- Part 3, `data/pano/<PANO_ID>/face_0..5.jpg`: the viewer highlights the best match by comparing `location.best_ref` with a face URL, a face name such as `face_2`, or an index. Please make `best_ref` one of those forms. If it is a reprojected reference view id instead, tell Part 2 and the viewer mapping will change.
- Part 3, `server/refs.py`: for the warm-up flow, Part 2 only calls `POST /api/capture/auto`. Part 1 calls `Localizer.add_reference`; nothing else is needed from Part 3.

### Needs a human

- A1 (Matterport credentials) belongs with Part 3. Part 2 never reads `.env.local`.
- A2 (about 2 minutes): install Expo Go, run `scripts/tunnel.sh`, scan the QR, tap Allow for camera, location and speech, and set the volume.
- A3 (the demo): the phone app has only been bundled, never run on a physical iPhone. Phone capture time is unmeasured; if over 250 ms, lower quality and size in `app/App.js`.
