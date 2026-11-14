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
