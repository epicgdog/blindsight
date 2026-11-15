# Voice-agent connection audit

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
