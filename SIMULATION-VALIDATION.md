# Repeatable integrated simulation

Run `make setup`, then `make simulation` for the focused simulated flow, or
`make check` for lint, formatting, all Python tests, and all JavaScript tests.
Node is required. Browser/video checks require Chrome, ffmpeg and ffprobe on PATH.
The focused simulation needs no real property assets, phone, or Gemini API key.

The new full-flow test generates a textured 3D room and panorama references at three
positions. It renders camera images, encodes the real binary frame protocol, streams
them through FastAPI to the real SIFT/HOG property localizer, checks a stable location,
and sends a WAV clip through the actual multipart voice endpoint. A deterministic
Gemini stand-in calls the real navigation tool; the test checks guidance, viewer
frames, simulated speech acknowledgments, a second scan match, and stale-location
expiry. The synthetic route graph belongs only to the generated open room.

Language comprehension, microphone capture, acoustic playback and physical phone
motion are not simulated faithfully. The Gemini stand-in is explicit, not an actual
cloud inference. The detector is a stub in this combined test; detector behavior is
covered separately by its Python tests. Passing this suite does not prove reliable
independent navigation or perfect operation on a real phone.

## Failures reproduced and deployment checks

`make check` previously stopped at its formatting gate: server/cues.py and
server/tests/test_protocol.py were not formatted. Both are corrected. The check
target now also runs JavaScript tests rather than checking only the server.

A bare Tailscale hostname previously defaulted to ws://, which made voice uploads
use http://. Bare .ts.net and .trycloudflare.com names now default to wss:// and
therefore HTTPS uploads. Explicit schemes remain respected; LAN IPs still use ws://.
Two direct audio uploads to the live HTTPS voice endpoint succeeded during these
audits. This does not prove the phone's network, recording or playback is working.

The combined simulation waits for the detailed cue's reserved speech period before
expecting navigation speech. Its initial fixed 1.5-second wait was too short for
longer orientation announcements and failed when run in the full suite.

The running minky.tailb9ac0d.ts.net service answered /healthz, but /api/agent/health
returned 404 during inspection. The latter endpoint is in main's voice fix; this
suggests the running process has not loaded that code. After updating/restarting,
/healthz must include voice_agent_api_version: 2 and /api/agent/health must return
the readiness summary. Check the actual service working directory and process if
the marker is absent. Keep API keys in the local ignored environment file.

Update the laptop checkout, run `make setup`, restart the FastAPI process serving
Tailscale, and reload/restart Expo with `npx expo start --clear`. Updating GitHub
alone changes neither a running server nor a cached phone bundle.

One full-suite test requires the real data/matterport/walls_floor1.json artifact;
without it, real-floor entrance connectivity remains skipped. The synthetic route
test covers the interface separately and never fabricates the missing real map.
