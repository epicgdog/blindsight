# Handoff: Gemini Live voice agent (2026-11-14, evening session)

Where the voice agent work stopped. The older integration notes from the part owners follow
below the line, unchanged.

## Run it

```sh
# server (own terminal; startup ~2 min). LOCALIZATION_SCOPE=single is required, see Known issues.
LOCALIZER=real LOCALIZATION_SCOPE=single uv run python -m server.server
# or, while editing code: models built once and kept in RAM, server code hot-reloads in ~1 s
LOCALIZER=real LOCALIZATION_SCOPE=single uv run python3 -m server.devserver
# phone app (Expo Go). Reload the app after pulling. The legacy screen has no on-device speech any more;
# expo-speech stays installed only for the native-navigation screen (app/NativeNavigation.js).
cd app && EXPO_PACKAGER_HOSTNAME=<your-tailscale-ip> EXPO_PUBLIC_SERVER_URL=wss://<your-funnel-host>/ws/phone npx expo start
```

- Key: `GOOGLE_API_KEY` in `.env.local` (read by `server/agent.py:read_env_key`; never printed).
  Verified working at the end of the session (it had run out of prepaid credit earlier: 402).
- Everything Gemini runs on API version **v1beta** (Live, function calling and TTS fail on v1).
- `AGENT=0` turns the agent off; the server is then silent (the old cue engine was removed). Server tests run that way.
- Tailscale funnel is public: anyone with the URL can spend Gemini credit through
  `/api/voice` and `/api/agent/talk`. Turn it off after the demo: `tailscale funnel --https=443 off`.

## What it does now

The phone has no on-device speech. One **Gemini Live** session per phone connection
(`gemini-3.8-live`) is the only voice:

1. **User talks** (hold-to-talk button): the app records m4a with expo-audio and POSTs it to
   `/api/agent/talk`. The server decodes it to 16 kHz PCM with ffmpeg and sends it between manual
   `activity_start` / `activity_end` markers (no voice activity detection).
2. **Server events**: each frame, `AgentState.on_frame` produces events and the server sends them to
   Live as `EVENT:` text. Events are the next guidance instruction, arrival, and a very close obstacle
   (seen 2 frames in a row, once per 6 s per class). The agent decides how to say them. The old
   cue engine was removed.
3. **Tools** are answered by code, never the model: `get_location`, `get_surroundings`,
   `start_navigation`, `stop_navigation`, `get_route_status`, `describe_view` (one gemini-2.5-flash
   vision call on the latest frame).
4. **Audio to the phone is streamed**: Live's 24 kHz PCM is cut into **50 ms WAV segments**
   (`SEGMENT_BYTES = 2400` in `server/agent.py`). Each one is pushed the moment it exists over the
   phone websocket as `{"type":"voice", turn, seq, first, last, interrupt, url}`. The phone fetches
   `/api/agent/clip/<id>` and plays segments back to back (`app/src/voice.js`). The first segment of an
   answer or an urgent warning interrupts; the rest queue. A clip that has not started after 6 s is
   skipped.
5. **Fixed app phrases** ("Insight ready", error lines) are Gemini TTS
   (`gemini-2.5-flash-preview-tts`, `/api/voice?text=`, cached in `data/voice_cache/`).

Supporting pieces:

- **Place decision** (`server/place_window.py`): a verified frame wins for its top place when
  it has >= N_MIN inliers and >= 1.3x the runner-up. 3 wins within 5 s decide the place (any
  verified frame counts, LOCKED is not required). With no decision it holds the last place;
  after 10 s the place is unknown. (The old one-frame confidence > 0.70 never fired once rival
  places were verified: 125 vs 80 inliers scores 0.48.)
- **Route to the entrance** (`server/entrance_nav.py`): the entrance is scan 28,
  `n0ppizy9u8xcadypd2bawceyc` (also `ENTRANCE_ID` in `server/viewer/logic.js`).
  - The graph is the 36 floor-0 scan points, joined when ≤ 6 m apart and the line between them
    crosses no wall in `walls_floor1.json`. Dijkstra finds the route.
  - Heading comes from the localizer yaw and the calibrated pano frame (`pano_frame.json`).
  - Instructions are spoken as clock directions, repeated every 10 s.
- **Object memory** (`server/scene_memory.py`): the current frame plus the last 60 s.
- **Log**: every frame, event and turn goes to `data/agent_log/<session>.jsonl`, for replaying a run.
- **Expo logs** (`app/src/log.js`): `time [tag] message {details}` with tags `voice`, `ws`, `talk`,
  `mic`, `loc`, `cam`, `app`. Every clip logs load / loaded / playing (startup_ms) / finished / cut /
  stalled. The slow-capture log was removed.

Measured with the key (in-process server, stub localizer, real Live API):

- "Take me to the entrance": the agent calls `start_navigation`. The first audio segment reached the
  phone socket at about 1.8 s, the full reply by about 5 s.
- Guidance and obstacle events: the whole turn took about 3 s. With streaming, first audio arrives
  much sooner.
- **Not yet heard on a real phone with 50 ms segments.**

## Known issues and next steps

1. **50 ms segments will probably be choppy.** Each segment is a separate HTTP fetch through the funnel
   plus a player load (likely 100–300 ms), far longer than 50 ms of audio. Check `[voice] playing
   startup_ms` in the Expo log. Fixes:
   - raise `SEGMENT_BYTES` to 12 000–24 000 (0.25–0.5 s), or
   - play raw PCM with a streaming player (e.g. `react-native-audio-api`). That needs an Expo dev
     build instead of Expo Go.
2. **`LOCALIZATION_SCOPE` defaults to `property`** (a teammate's change). That routes to
   `server/property_localize.py`, which does not fill `match.ranking` the way `server/localize.py`
   does, so the place window never becomes confident. Start with `LOCALIZATION_SCOPE=single`, or
   make the agent read the property localizer's output.
3. **Guidance turn direction is unverified on site.** Walk the route and check that "turn right"
   is right.
4. The 1.3x margin and 3-win rule are tuned on one logged run only.
5. Live sometimes embellishes ("it's right ahead"). The prompt forbids it; watch for it.
6. A Live session drops on errors and reconnects on next use, but loses the conversation.

## Git state (end of session)

- `origin/main` = `945258b`: teammates' 5 commits plus our 23 cherry-picked on top (all tests passed
  then). Backup of the pre-rebase branch: `origin/feat/voice-agent`.
- **Uncommitted, ours** (Live agent, streaming, Expo logs, TTS, no on-device speech in the legacy screen):
  - `server/agent.py`, `server/voice.py`, `server/server.py`, `server/tests/conftest.py`,
    `server/tests/test_agent.py`
  - `app/App.js`, `app/src/{voice,log,talk,audio}.js`, `app/test/unit/{voice,log,talk,audio}.test.js`
  - `app/package.json` / `app/package-lock.json` (expo-file-system added; expo-speech kept for NativeNavigation)
  - Tests at the end: 310 server, 76 app, all passing.
- **Uncommitted, not ours** (another session or teammate; ask before committing):
  - `server/viewer/*` and `map3d.js`, `vendor/` (3D map)
  - `server/config.py`, `server/tests/test_match_payload.py`, `tools/eval_pitch_gate.py`
  - untracked `HARDCODE.md`, `IMPLEMENT.md`, `VOICE_AGENT.md`, `SESSION-HANDOFF.md`
- Commits in this repo carry no Co-Authored-By line (user rule).

## Decisions made this session

- Retriever: dinov2-base was A/B tested against small (`tools/eval_retrieval.py`). Not better, so
  small stays.
- Matcher: LoMa dropped; SuperPoint+LightGlue stays.
- `HARDCODE.md` (single floor, OCR/YOLO to tell look-alike sides apart) is a plan only, not
  implemented on purpose.
- Meta Llama API was not used (no speech). Gemini only.

---

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


---

# Part 3 handoff

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
