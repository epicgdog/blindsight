# NOTES part 2

Branch is `part_2` (the brief says `part2-app`; the requester asked for `part_2`).

## Assumptions (made without asking, per the brief)

- Part 1's server and Part 3's `tools/synthetic_feed.py` are not in this repo yet, so everything runs against `app/dev/echo_server.py`. It also serves the viewer and the 5.3 endpoints, so it is longer than the brief's 30 lines. Switching to the real server is a URL change.
- `best_ref` is treated as either a face index or a face URL by the viewer.
- Cue ids are what the phone dedupes on; the echo server repeats the location cue id so dedupe is exercised.
- `tunnel.sh` bakes the server URL into the Expo bundle via `EXPO_PUBLIC_SERVER_URL`, so the QR Metro prints opens the app with the address filled in. Metro itself is served over the LAN; pass `BLINDSIGHT_EXPO_ARGS=--tunnel` if venue Wi-Fi isolates clients.
- Backup video uses a built-in placeholder pano until Part 3's feed exists, and the echo server instead of the real pipeline. It is labelled SIMULATED in the video and in the render log.
- The viewer test and video renderer use the macOS Chrome path; override with `CHROME`.
- Expo SDK 58 packages were verified to exist and `expo export --platform ios` bundles; the app has not yet been run on a physical iPhone.

## Not yet done / human steps

- A2 (Expo Go install, scan, Allow prompts) and A3 (demo) are human steps.
- Phone capture time on a real iPhone is unmeasured. If over 250 ms, lower quality/size, then consider react-native-vision-camera with a dev build.
- Three timed rehearsals with a blindfolded volunteer are not done.

## Measured numbers

None yet. The pitch uses no numeric claims. Add real measurements here before quoting them.
