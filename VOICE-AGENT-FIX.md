# Voice-agent connection audit

## Latest-main Gemini Live follow-up — November 14, 2026

Baseline: main at `59cf64d`. The current app uses Gemini Live audio segments and
expo-audio; the earlier audit below describes the preceding voice architecture.

The phone previously discarded HTTP reply audio whenever camera streaming was
running, even if the socket delivered no voice segments. Replies now carry the
same turn ID as their streamed segments. The app plays the HTTP clip when that
turn has no received segments, suppresses duplicate replies, and ignores late
segments after HTTP fallback playback. This addresses missing delivery, but does
not prove that received audio played audibly on a physical phone. Stalled players
are paused before advancing the queue, preventing delayed playback overlap.

The server bounds Live connection and turn completion together at 45 seconds.
Already-compatible 16 kHz mono PCM WAV uploads decode without FFmpeg; phone AAC
uploads still require FFmpeg. The standard lint/format gate is repaired. The
inference-responsiveness test now checks that health responds while inference is
blocked, instead of asserting an unreliable 80 ms wall-clock threshold.

Observed integration evidence:

- A real local Gemini Live request transcribed “Where am I?” and returned audio
  and three socket segments in 4.4 seconds using the locally configured key.
- Gemini TTS produced a valid 24 kHz WAV containing 1.49 seconds of audio.
- The public Tailscale service initially returned 502, then recovered. A request
  through that service transcribed the question and returned an audio URL in
  4.95 seconds. Its clip contained four seconds of 24 kHz audio, and the iOS byte
  range probe returned HTTP 206. That deployed response lacked the new turn ID,
  confirming it still needs this server update.
- The Expo iOS bundle exports successfully. Phone microphone, speaker output,
  and an on-site walkthrough remain unverified by these checks.
- `make check` passes lint, formatting, the Python regression suite, and all 136
  JavaScript tests. The property entrance-connectivity test is skipped because
  the ignored `data/matterport/walls_floor1.json` asset is absent; that route has
  not been validated by this checkout. AAC-to-PCM conversion was also checked
  separately with FFmpeg.

Deployment: pull main and restart FastAPI on the serving laptop, then reload the
updated Expo app. Keep GOOGLE_API_KEY in the ignored `.env.local` on that laptop;
the key saved in this checkout is not transferred by Git. Check `/healthz` and
`/api/agent/health`, ask a short question, and verify both the transcription and
audible playback on the phone. Connection success alone does not establish
localization: the inspected service reported `localization_scope: single`.

## Earlier request/response audit

The deployed /api/agent/talk endpoint was tested with a synthesized "Where am I?"
WAV clip. It transcribed the words, called get_location and returned HTTP 200 with
"I am not sure yet. Please look around slowly." in about five seconds. This proves
the deployed backend accepted that request, not that microphone/upload/playback on
the iPhone works. Location was unknown in /api/agent/state at the time of inspection.

The phone's "I can't reach the voice assistant" is the client fetch catch message:
it previously conflated network failure, timeout and response-parsing failure. The
phone aborted at 20 seconds, while conversion and several Gemini calls could take
longer. It now waits up to 65 seconds, reports timeout separately, and distinguishes
busy/unready HTTP responses. Gemini transport calls now have a 10-second timeout.

Recording preparation and button release were independent async callbacks: release
could call stop before microphone permission/preparation completed. A serialized
controller now waits for readiness, measures duration from actual recording start,
restores playback mode, and prevents a second recording during upload/thinking.
Hold until "Listening" appears before speaking. Very short clips are discarded.

The property's last_debug candidates were also not being adapted into the ranking
format used by AgentState. They now are. Non-LOCKED or explicitly rejected frames
cannot become fresh agent location evidence. PlaceWindow now prunes old frames on
reads as well as writes, and state/tool reads expire location when camera frames stop.

Voice turns are serialized server-side so overlapping uploads cannot corrupt one
conversation. Upload reads are bounded. GET /api/agent/health reports ready,
key_configured, model and ffmpeg_available without exposing credentials. Server-side
logs retain failures; the API speaks a safe message. No new API key was committed.

Deploy this branch on the FastAPI laptop and reload the Expo app: a server restart
alone cannot fix the phone recording race or update its old 20-second timeout.
Keep the existing Tailscale URL. Test a short question while watching /api/agent/health
and /api/agent/state. If the phone still reports a network error, check its console
and server access log for whether the multipart request arrived. A successful upload
with unknown location requires visual-localization work, not another Gemini key.

No physical iPhone microphone/playback check was possible here. Do not represent
the direct backend audio test as a complete physical-device acceptance test.
