# Detailed cues for the Expo / FastAPI phone flow

The existing streaming endpoint and Expo client need no protocol change. Detailed
speech is enabled by default on the server. Set DETAILED_CUES=0 for legacy phrasing.

Examples: "2 people ahead to your right, about 1 o'clock", "Stop. Chair directly
ahead", and "View matches the hallway". Object bearings are relative to the camera,
not necessarily the user's body: keep the phone pointing in the facing direction.
HFOV_DEG must match the actual phone camera field of view for bearing accuracy.

Counts describe objects of the same class in the same direction/proximity bucket,
not every object in the scene. Directions use a pinhole horizontal projection and
clock sectors; they do not establish floor position, depth, clearance, or a detour.
The old box-area proximity proxy still controls priority, but is no longer spoken
as a measured distance. Detection JSON adds bearing_deg, distance_source and null
distance_m so viewers can distinguish evidence from measurement.

For scan-specific descriptions create git-ignored data/cue_landmarks.json containing
a list of reviewed observations, for example these fields (not actual hall data):
pano_id, label, yaw_deg, reviewed. yaw_deg must be the landmark bearing in the SAME
panorama coordinate frame used by Part 3's yaw_deg. reviewed must be true. Determine
labels and bearings by inspecting that panorama; do not fill them from guesses.
Landmarks are announced only for the matched pano with LOCKED state and heading
confidence >=0.7. Wording is "The scan shows ... at about ... o'clock". These
annotations do not claim the movable object is still present or give a meter range.
Repeated landmark announcements have a 45-second cooldown per bearing sector.

To deploy: merge this branch into the server's checkout and restart its existing
FastAPI process with its existing environment and data. Keep Expo and Tailscale
configuration unchanged. This code does not remotely restart the friend's server.

Inspection of https://minky.tailb9ac0d.ts.net on November 14 found YOLO/real-localizer
mode, hallway label, no active phone, and no speech acknowledgments. Its embedded
simulated report also had 1 false lock in 20 negative walkthroughs. That is separate
from narration detail and needs localization tuning/validation before trusting a
match as an exact current location. Prior successful reports from other configs do
not override this deployed report. No live phone accuracy or latency was measured.

## More descriptive cues (detailed mode)

All of this is detailed mode only; `DETAILED_CUES=0` keeps the old phrasing exactly.

- More things are named. Extra obstacles: bed, toilet, dog, cat, umbrella, bag, fire
  hydrant. Things that say what kind of place this is, never urgent, lowest priority,
  each named at most every 30 s: laptop, fridge, sink, microwave, oven, clock, stop sign,
  traffic light. Small items YOLO often mislabels (cup, bottle, book, phone) stay silent.
- Several far things in one sentence (up to 3): "2 people at 1 o'clock and a bench at
  11 o'clock." Close and urgent warnings are still said alone and first.
- "Getting closer": a box that grows by 40% within about a second, and is not cut off
  by the image edge, gives "Person directly ahead, getting closer." at the priority of a
  close object. It says the gap is shrinking, not who is moving, and no distance.
- Arrival says what is in view: "View matches the hallway. In view: 2 people and a
  bench." Those are not repeated one by one straight after.
- No cue is sent while the phone is probably still speaking the last one (estimated at
  2.5 words per second). The phone drops a cue that arrives mid-speech, so longer cues
  would otherwise lose the next one. Urgent "Stop." warnings still interrupt at once.

To hear the difference on a real walk without walking again: run the server with
`RECORD=1`, walk with the phone, then on any laptop (no YOLO or torch needed):

    uv run --no-project python -m server.cue_replay                 # newest data/sessions/*
    uv run --no-project python -m server.cue_replay data/sessions/<stamp>

It prints every cue with its time, old wording ("before") next to the new ("now").
