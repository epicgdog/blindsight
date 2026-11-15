# November 15 regression repairs

Baseline: `a48aa1b`. This follow-up repairs the failures recorded in the voice audit
and adds coverage for the property-wide voice integration and stale arrival state.

## Changes

- Geometric matching retains distant candidates until the tracker checks them.
  Previously, removing the strongest distant candidate let a weaker nearby
  look-alike move the track, interrupting recovery and causing false locks. The
  rendered-room regression now also rejects intermediate wrong-place transitions.
  Distance gating, stationary-phone holds, distinctive evidence and repeated-fix
  recovery checks remain enabled.
- Property recognition publishes verified rankings, match validity and visual
  heading observations using the same interface as the other localization engine.
  A test recognizes rendered images, establishes voice-agent location/heading, and
  obtains a navigation instruction. Rejected frames supply no heading observation.
- Coverage metadata reports the actual indexed candidates instead of defaulting
  to one scan. A previous arrival cannot override a new location away from the goal.
- Camera tests reflect the focal/lens EXIF data used for intrinsics calibration.
  Viewer tests exercise the current ranking and map layout, including browser
  updates, rather than asserting the existence of intentionally removed panels.

## Verification

Run `make check` with `uv`, Node, Chrome, FFmpeg and ffprobe available. It checks
lint/formatting, the Python suite, and the JavaScript suite. The Python suite also
runs headless Chrome viewer checks and renders/probes a short H.264 MP4; those
checks are not skipped in this environment. Final `make check`: **483 Python
tests passed in 109.37 seconds**, **154 JavaScript tests passed**, no failures or
skips. Lint and the required formatting checks also pass.

The entrance-connectivity test now runs against local assets exported from the
serving laptop's `/api/pano` response. The response identifies model
`P3S6VUTP3qB`, 66 scan points and 726 floor-zero wall segments. The point positions
were copied to the input `position` fields without a coordinate transformation;
wall endpoints were copied as supplied. Source/model metadata accompanies the
local wall file. This checks graph connectivity with the serving map, not physical
clearance or an independent survey. It is not a newly unlocked mesh/depth export.

Downloaded assets and `.env.local` remain ignored. A different checkout needs its
property assets to run the same entrance test; without them it will explicitly
skip. No credential or property asset is included in this change.

## Applying the update

Pull main and fully restart FastAPI on the serving laptop. `server.devserver`
retains localization modules in memory, so hot reload alone is insufficient for
this localizer change. Existing phone code is compatible. Confirm `/api/pano`
reports the intended scan coverage, stream the camera, and inspect
`/api/agent/state` while making a short spoken destination request.

Real iPhone microphone/speaker behavior, on-site location/heading accuracy,
obstacle-warning audio latency and independent route completion still require
physical-device measurements. Passing the suite does not establish those gates.
