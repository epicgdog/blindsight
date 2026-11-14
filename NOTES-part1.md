# Part 1: core server

Implemented against the final HackPrinceton plan and the repository's Part 1 brief.
Branch: `part-1` (Git does not permit spaces in `part 1`). Main and teammates' branches
are not modified. Part 2/3 source ownership is preserved.

## Run commands

Python 3.11–3.13 and [uv](https://docs.astral.sh/uv/) are required. From the repository root:

```sh
make setup                 # uv sync --locked
make download-models       # online once; caches YOLO in data/models/
make check                 # lint, format, and core tests; no model download
make server                # offline-capable YOLO + explicitly UNCERTAIN localization stub
```

The server prints the laptop's LAN HTTP and WebSocket addresses and binds `0.0.0.0:8000`.
The plan's `cd server && uv run python server.py` also works. Use one Uvicorn worker:
the active frame, localizer, recorder and viewer connections belong to that process.

Useful environment overrides:

```sh
DETECTOR=stub LOCALIZER=stub make server  # lightweight transport development
LOCALIZER=real make server               # requires Part 3's server/localize.py + assets
RECORD=1 make server                     # save processed frames/headers/results locally
YOLO_DEVICE=cpu make server              # explicit CPU fallback
PORT=8001 YOLO_IMGSZ=480 make server     # alternate port / thermal fallback
```

Missing weights fail startup with an actionable message. Startup never silently downloads
weights unless explicitly enabled with `ALLOW_MODEL_DOWNLOAD=1`. `yolo11n.pt` is primary,
`yolov8n.pt` is the cached fallback. MPS failures retry on CPU. Model files are ignored by Git.
YOLO's PIL input preserves RGB color order; boxes are normalized to the upright decoded frame.

Simulator commands (in another terminal):

```sh
make sim ARGS='--webcam 0 --fps 6'
make sim ARGS='--video path/to/demo.mp4 --fps 6'
make sim ARGS='--images path/to/images --loop --seconds 60 --fps 6'
make sim ARGS='--replay data/sessions/SESSION'
make sim ARGS='--synthetic --loop --seconds 60'
make soak ARGS='--replay data/sessions/SESSION --output data/reports/soak.json'
```

Add `--url ws://LAPTOP_IP:8000/ws/phone` or a `wss://` tunnel URL as needed. Replay uses
recorded monotonic receive offsets, including dropped-frame gaps, but sends fresh sequence
numbers/client timestamps so RTT remains measurable. The default soak is ten consecutive
180-second runs, reconnecting between runs. It fails on disconnect, missing results,
invalid frames, pipeline errors or a shortened run. A shorter `--seconds`/`--runs` is for
development only, not evidence for the required 30-minute gate.

## Wire contract and team integration

`server/protocol.py` is the shared import for `Detection`, `Location`, `Cue`, `FrameHeader`,
`encode_frame` and `decode_frame`. The nine positional Location fields from the plan stay
unchanged; `ema` is an optional final field (defaults to score on the wire).

Input: 4-byte unsigned big-endian JSON length, UTF-8 JSON header, JPEG bytes. Required
header fields are `seq`, `ts_ms`, `heading_deg`, `w`, `h`. `heading_deg` is null or [0,360).
Headers are limited to 16 KiB, messages to 4 MiB, decoded images to 12 megapixels. Actual
decoded dimensions override the header after EXIF orientation is applied.

Both result sockets carry `seq`, `server_ms.{decode,yolo,embed,total}`, `detections`,
`location`, `cue`. Additive fields: echoed `ts_ms`, upright `w`/`h`, and explicit `mode`.
`/ws/viewer` also carries **`jpeg_b64`**, matching the existing Part 2 viewer.
`distance` is `"very close"`, `"close"`, `""` (omit distance word), or null (ignored tiny object).
Direction and distance are image-based descriptions; they do not claim metric depth.

One active phone/simulator is supported. A second source receives WebSocket close 1013 and
should retry with backoff. Each connection has one pending latest frame; incoming frames
replace it while a dedicated worker runs inference. Viewers have independent one-item
queues, at most 10 updates/s, and a send timeout. Slow viewers cannot block inference.
Invalid framing closes with 1008 (text input: 1003); inference failures close with 1011.
Reconnects get fresh cue/temporal state; resettable localizers retain loaded models and verified references. Cancellation waits for in-flight model work and
closes the recorder before releasing the source slot.

`Localizer()` keeps the plan contract. If its constructor accepts `data_dir`/`pano_id`, the server passes configured paths; otherwise it uses the no-argument form. Part 3's `reset()` allows one startup model load across reconnects. `update(rgb_uint8, detections, heading_deg)`
runs once per **processed** frame after YOLO; Part 3 owns the embedding cadence using
`EMBED_EVERY_N_FRAMES=3`, temporal fusion and compass prediction. The server does not skip
updates because doing so would distort Part 3's consecutive-frame state machine.
`add_reference(rgb_uint8)` is called serially on the same worker during warm-up. Only a
true return value leads to an on-site saved frame. The stub never returns a verified match,
never accepts a reference, and never emits a location cue.

Endpoints:

| Endpoint | Behavior |
| --- | --- |
| `GET /` | Part 2's viewer, or honest core-ready placeholder when absent |
| `GET /healthz` | Startup readiness and detector/localizer modes |
| `GET /api/pano` | `pano_id`, label, `faces`/`face_urls`, calibration `thresholds` |
| `POST /api/capture/pos` or `/neg` | Save latest upright JPEG; 409 before any frame |
| `POST /api/capture/auto?seconds=20` | Start asynchronous warm-up; 409 without source/already running |
| `GET /api/capture/auto` | `running`, `seconds`, `elapsed`, `kept`, plus attempted/accepted/errors |
| `GET /metrics` | Rolling p50/p95 per-stage latency, fps, drop/error counts, lock time, audio evidence |
| `POST /api/speech` | Optional phone acknowledgment, described below |

Pano selection is `PANO_ID`, Part 3's `data/pano/pano.json`, or the sole directory under `data/pano/`. Reference thumbnails from `refs/refs.json` are exposed as `refs[].path` URLs. Label comes from
`PANO_LABEL` or saved `panoLocations.json`. Multiple directories without selected-pano metadata require explicit PANO_ID;
the server does not guess a real place. Only `data/pano/` is publicly served: sessions,
on-site captures, negatives and credentials are not mounted.

## Audio and full-demo evidence

The server cannot measure when an iPhone speaker becomes audible. `server_ms.total` is
server receipt through processing/recording; simulator RTT is a separate client measurement.
`capture_to_audible_ms` remains null until the phone sends measured playback acknowledgments.
The existing Part 2 app does not yet send them; see HANDOFF.md.

After Expo's actual startup speech callback, POST `{"played":true}` to `/api/speech`.
For a cue, POST `{"played":true,"cue_id":"ID_FROM_RESULT","capture_to_audible_ms":430}`,
where elapsed time is measured on the **phone's monotonic clock from camera capture to
speech start**. Sending a cue without a latency acknowledges playback but adds no latency
sample. Unknown, expired or duplicate cue IDs receive 409; dropped speech must not be
reported as played. A software callback is playback evidence, not an acoustic measurement.

`make self-check` prints PASS/FAIL for cached YOLO inference, real localizer, six readable
equal-sized square faces, numeric ordered calibration thresholds, and phone speech
acknowledgment from the running server. It intentionally exits nonzero while Parts 2/3
assets or playback evidence are missing. A successful stub run is not full-demo readiness.

`make demo-check` runs a 60-second synthetic feed through the server and enforces 4 fps,
RTT p95 <300 ms, time to LOCKED <2 s, audio p50 <500/p95 <900 ms, and the Part 3 evaluation
targets. Missing evidence fails loudly. It does not lower thresholds to obtain a pass.
Part 3's native `tools.synthetic_feed.iter_feed()` / `FeedFrame` objects are supported, including compass headings. An `iter_frames(fps, width, height)` array/tuple generator is also accepted. Exporting images and passing `--images DIR` is also
supported. No substitute pano or made-up localization score is generated by Part 1.

Part 3's native `data/eval/results.json` is supported directly, including all of its target pass/fail values. `make demo-check` runs its evaluator with `--no-stress` before streaming. The raw report is exposed under `/metrics.localization_accuracy`. A normalized report at `data/reports/localization.json` (or `--eval-report FILE`) is also supported, with these keys:

```json
{
  "split": "held-out",
  "simulated": true,
  "false_locked_rate": 0.0,
  "recall": 0.95,
  "negative_walkthroughs": 20,
  "false_locked_walkthroughs": 0,
  "locked_within_2s_trials": 20,
  "yaw_median_deg": 5.0,
  "yaw_p95_deg": 15.0
}
```

These numbers illustrate the schema only; they are **not measured project results**.
Rates are fractions in [0,1]. The report must come from Part 3's held-out evaluation.

## Measured verification

All measurements below are local Mac tests, not lobby or physical iPhone results.

- Core lint/format and 72 tests passed at the initial verification checkpoint. Tests cover
  packet framing and malformed input, EXIF rotation, normalized coordinates, direction and
  distance boundaries, 3-frame debounce, cooldown, P0 precedence, cue storms, latest-wins
  backpressure, event-loop responsiveness, reconnects, viewer output, verified-only capture,
  record/replay, speech evidence, cached-weight/CPU fallback behavior and strict metric gates.
- Real YOLO11n, MPS, `imgsz=640`, 60 seconds at requested 6 fps: **360/360** frames returned,
  **6.00 fps**, RTT p50 **50.44 ms**, p95 **64.22 ms**; server p50 **39.60 ms**, p95 **54.55 ms**.
  Source: repeated public Ultralytics `bus.jpg`, not a synthetic Matterport feed.
- With socket connections disabled, cached YOLO11n loaded, warmed up and detected people
  in **20/20** repetitions of the same test image. This verifies offline inference, not the
  separate chair/person/table acceptance test in lobby lighting.
- Part 2 branch `dac6162` tested read-only via a detached worktree: actual viewer loaded
  frames, 5 detection boxes, cues and the complete 20-second warm-up; zero JS errors or
  failed requests. The stub correctly kept 0 references. No Part 2 files were edited.
- The actual Part 2 JavaScript encoder/client delivered **20/20** frames to real YOLO and
  invoked its speech adapter with object cues. The adapter was simulated; no phone audio
  claim is made.
- The 10 x 180-second recorded-session soak is run separately; final results are appended
  below after completion. Localization stays explicitly `UNCERTAIN` in that run.

## Remaining integration/physical checks

Part 3's real localizer, panorama, calibration and held-out accuracy report are absent
from this branch. Physical iPhone camera latency, venue-network RTT, speech audibility,
lobby-object detection and on-site localization accuracy cannot be verified from this
workspace. Full `self-check` and `demo-check` must remain FAIL until those inputs exist. Part 3 branch `5d501a6` is available remotely and was tested in an isolated combined checkout, not merged into this branch.
Part 2 owns tunnel/QR, app, viewer, slides and backup video. No deployment or merge into
main is performed by this Part 1 implementation.

The initial Git CLI push was blocked by a missing credential-helper executable; the
GitHub connector also rejected writes (403). The existing signed-in browser session is
used to publish the branch without changing account permissions or creating credentials.

## Final Part 1 verification

- `make check`: **78 passed**, Ruff lint and format checks passed.
- Ten consecutive 180-second recorded-session runs completed: **1800.44 seconds**,
  **10,810/10,810** frames returned, **0** errors, invalid frames or dropped frames.
  Per-run throughput stayed **6.002–6.005 fps**; worst per-run p95 RTT was
  **140.08 ms**. Each run reconnected successfully without restarting the server.
- This soak used real YOLO11n/MPS and the stub localizer; it proves Part 1 stability, not
  Part 3 localization accuracy or phone audio latency. Some runs overlapped browser/ML
  integration checks on the same machine. The final API/config validation changes were
  covered separately by the 78-test suite and real viewer/client integration.
- Full-demo readiness correctly reports missing real localizer, pano, calibration and
  physical-phone speech acknowledgment. Those gates were not waived.

## Part 3 integration follow-through

- Reviewed the actual Part 3 branch `5d501a6` and adapted only Part 1-owned files.
- Native `iter_feed()`/`FeedFrame` and image-directory `frames.jsonl` headings now work.
- Native `data/eval/results.json` is consumed without asking Part 3 to change its schema.
  Its additional per-candidate latency and overall-localization-rate targets remain enforced.
- Pano selection reads `pano/pano.json`; reference thumbnail paths are served without
  exposing private sessions or captures. Manual negatives call `add_negative` immediately.
- Localizers offering `reset()` retain model/reference caches across reconnects; only
  temporal state resets. `server_ms.embed` uses Part 3's measured embedding duration.
- LightGlue is installed at pinned upstream commit
  `5a650c444718caffc2669dc0094cde908f42dd73`; LightGlue and SuperPoint imports passed.
  Part 3 still controls matcher/retriever selection and weight prefetching.
- Actual YOLO11n + Part 3 SIFT/HOG fallback ran through the server's WebSocket handlers
  on Part 3's procedural room: both sessions reached LOCKED on the second frame, emitted
  two location cues total, kept two geometrically verified warm-up references across
  reconnects, and reported zero errors. Its real synthetic-feed adapter also passed.
  **This is procedural-room verification, not lobby accuracy.**
- The first combined Part 1 + Part 3 suite passed 119 tests. The expanded core suite
  passes 82 tests after adding native report/feed, pano metadata, reset and negative tests.

## Delivery checkpoint

The final isolated combined checkout passed **123 tests in 43.51 s**; the Part 1-only
checkout passes **82 tests**. Neither teammate branch nor main was modified.

The remote `part-1` branch was created through the signed-in browser, but it still
points to the original main commit: source upload is blocked by the extension's
local-file permission, and the connector still returns 403 on repository writes.
All implementation commits are preserved on local `part-1` and in the delivered
Git bundle. No account permissions or credentials were changed.
