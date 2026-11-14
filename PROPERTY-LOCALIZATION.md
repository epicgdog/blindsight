# Continuous whole-property visual localization

LOCALIZER=real now defaults to LOCALIZATION_SCOPE=property. The old single-scan
localizer is retained for evaluation using LOCALIZATION_SCOPE=single. Existing
single-point calibration/evaluation tools continue testing that original algorithm;
their scores must not be represented as whole-property accuracy.

## Deploy on the laptop serving the phone

Update the code, then download every scan using existing local credentials:

```
uv run python scripts/fetch_matterport.py --model P3S6VUTP3qB --neighbors 65
LOCALIZER=real LOCALIZATION_SCOPE=property uv run python -m server.server
```

Keep the existing Tailscale URL and Expo phone client. Include the phone orientation
fix from this branch; reload its Expo bundle. Stop the old FastAPI process before
starting the replacement on the same port. This branch does not restart a remote
laptop automatically. The API must supply the same model used by the property index.

GET /api/pano now reports localization_scope, indexed_scans and indexed_scan_ids.
If the saved API manifest lists a panorama whose six faces are not downloaded,
startup fails with the missing count and fetch command. Partial download coverage
is never silently presented as complete building coverage.

## Runtime behavior

The index includes every complete downloaded scan with uniform 15-degree horizontal
reference views. Every processed frame computes global retrieval and local-feature
vocabulary scores across all places. A bounded shortlist of the best six retrieval
places, four local-feature places, and the tracked place is geometrically checked.
At most two distinct reference views per shortlisted place are verified per frame.
Temporal identity never follows an unverified nearest-neighbor similarity alone.

The best location must pass geometric inlier count, image-grid coverage, heading
evidence and a cross-place margin of at least 1.25 (or configured PLACE_RATIO if
stricter). Exact ties and captured unknown-place negatives cannot become locations.
Location changes reset temporal evidence; the new place needs consistent frames
before LOCKED. Rejected frames immediately clear the published place, avoiding a
stale confident location. This conservatively favors uncertainty over false certainty.

Diagnostics list indexed scans, candidate scan, rejection reason, candidate inliers,
and competing inliers. The published pano_id changes when a different scan locks.
Automatic warm-up reference addition is disabled because new frames need verified
place identities; unregistered examples must not contaminate another place's index.

## Meaning and limits

This recognizes the most supported scan viewpoint and yaw. It is not a continuous
metric x/y estimate between scans, nor a verified floor/room navigation system.
Yaw is in the winning panorama's frame; it is not globally aligned across scans.
Room labels are spoken only when available from stored metadata; numbers are not
invented room names. Raw camera field of view still requires phone calibration.
The original rotation-based geometric verification is strongest near scan centers;
walking between them, repeated architecture, changed lighting and furniture require
real-phone evaluation. Do not lower thresholds just to force a lock.

Full property loading increases startup time, memory and per-frame work; extraction,
model selection and actual phone-to-audio latency must be measured on the demo laptop.
Current tests prove multi-place selection, transitions and rejection on a procedural
room, not all-66-scan accuracy in Rabinowitz Hall. Real-property held-out phone frames
and walkthroughs remain required before claiming reliable building-wide localization.

## Teach it places with phone photos

Real iPhone photos close the gap between Matterport renders and the phone camera, and
give each place a spoken name (Matterport labels nearly every scan "Hallway").

1. `open data/matterport/topdown.png` and note the scan number of each place you want.
2. Stand on that scan's dot. Phone in portrait, 1x, chest height. Take about 12 photos
   turning a full circle. Convert HEIC to JPEG (`sips -s format jpeg *.HEIC --out jpg/`).
3. Put them in `data/onsite/places/<scan number>_<name>/`, for example
   `data/onsite/places/34_front_door/`. The words after the number are spoken
   ("front door"). The folder may also start with the full pano id.
4. Restart the server. Startup prints, per folder, how many photos were added and which
   were skipped. `GET /api/pano` lists the same under `photo_places`.

Each photo is verified against its scan's panorama views (same inlier and grid tests as
a lock) and stored with the pano-frame yaw the verification recovers. A photo that does
not line up with its scan, or that another scan or an unknown-place photo explains
better, is skipped: a mislabelled folder cannot teach a wrong place. Photos therefore
only help where they at least weakly match the scan; they then make later phone frames
match much more strongly. At most 40 photos per folder are used.

Test with a different set of photos than the teaching ones:

```
make -f make/part3.mk p3-places-test DIR=$HOME/Desktop/place_tests
```

`place_tests/34_front_door/*.jpg` are expected to be recognised as scan 34;
`place_tests/elsewhere/*.jpg` (any folder that is not a scan number) must not match.
Each photo is judged on its own (no temporal fusion), with the result and the reason.
