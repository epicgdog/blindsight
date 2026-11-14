# Native spatial navigation implementation status

Merged integration: the established Expo/voice-agent screen remains the default.
Set EXPO_PUBLIC_NATIVE_NAVIGATION=1 when building/running the native development
client to select the experimental ARKit interface. Expo Go always uses the existing
phone flow. Both API families are registered; native movement gates remain closed
until their documented geometry and physical-validation requirements are met.

Feature branch: feature/independent-navigation. Intended product: independent
navigation for blind and low-vision users. This branch does not claim readiness for
independent walking. Several required external assets and physical tests are missing.

## Implemented

- Native Expo module: single ARKit camera session, camera intrinsics and row-major
  camera-to-native-world transform, timestamped sampled JPEGs, Core Motion steps,
  depth capability reporting, local central-depth stop warnings, and camera-loss
  watchdog. Native warning speech does not require the server or JS bridge.
- Native app: accessible start/stop, questions by on-device speech recognition or
  text, repeat and orientation controls. Expo Go retains the original camera client.
  A native build selects the new interface and never opens two camera sessions.
- Session APIs under /api/spatial: create session, tracking updates, grounded commands,
  and readiness. Tracking is latest-wins on the phone; server mutation is serialized.
  Sessions expire, out-of-order packets are rejected, tracking gaps invalidate fixes.
- Metric localization solver: SIFT correspondence proposals, ratio/uniqueness tests,
  spatially distributed RANSAC PnP inliers and positive-depth checks. Two agreeing fixes
  establish the native-to-map transform. Pose confidence decays with time and travel.
- Stateful route segment progress, measured-distance guidance, conservative stride
  calibration, proactive verified landmark narration with repetition limits, relative
  directions, grounded questions and deterministic destination requests.
- JS speech prioritization, expiry, cancellation and stale-tracking watchdog.
- Property preparation command exports room/floor geometry and all 66 scan positions
  without treating neighboring scan points as reviewed walkable edges.

## Verified external blockers and remaining implementation

Authenticated API inspection for P3S6VUTP3qB returns two OBJ assets (50k and 500k),
both status `locked`, with no download URL. Ask the organizers to unlock metric mesh
or provide registered depth/3D image landmarks. No purchase/unlock mutation was run.

The downloaded panoramas still support the original viewpoint demo, but cannot by
themselves supply a verified metric camera pose. Automatic mesh-to-image landmark
construction and route-clearance review remain to be completed after geometry access.
The existing PnP solver consumes registered landmarks; it does not manufacture them.

Native code warns about nearby depth occupancy and optionally runs the bundled
YOLO CoreML model through Vision for named visual warnings. The detector was exported
and compiled locally; it must be generated before native build (command below).
Precise depth for recognized objects, full-body clearance, and verified local detours
are NOT implemented. `depth_clear` deliberately
remains false in the native module: it must not issue movement commands based on an
unvalidated central image crop. No numerical latency or obstacle recall is claimed.

The localizer's numeric physical-pose validation gate also remains closed. Do not
set physical_pose_validated merely to make the demo advance. Obtain measured error
bounds from independent ground truth first. Full route review, depth-confidence
filtering, low/drop-off obstacles, moving-object tracking, and device-calibrated
uncertainty remain acceptance work, not completed capabilities.

Xcode lists a paired iPhone 14 Plus as unavailable; no iOS simulator runtime is installed. Native project
generation, autolinking, JavaScript export and Swift syntax parsing are checked;
these do not substitute for an actual native compile. CocoaPods installation is
blocked by system Ruby 2.6 versus its dependency's Ruby >=3.3 requirement.

The supplied CoStar kit was also inspected: its 47 archive entries contain listing
datasets, schemas and documentation, not the registered Matterport OBJ/depth export.
Resupplying the same API key does not change the `locked` asset response.

## Verification in this implementation

The 5.2 MB NMS-enabled YOLO11n model successfully exported with coremltools 9.0 and
NumPy 2.3.5, compiled with Apple's coremlcompiler, and loaded through Vision on the
Mac. A public bus fixture produced four labeled observations (bus, three people).
One Mac inference took about 21 ms; this is neither an iPhone measurement nor an
end-to-end warning-latency claim. The native detector uses the same Vision model API.
Swift syntax parsing, Expo autolinking and iOS JS export pass. Native compilation
and physical-phone acceptance remain unverified.

## Run and build

1. At repo root: `make setup`, then `uv run python tools/prepare_spatial.py`.
   It writes git-ignored data/spatial/source.json and readiness.json, including the
   actual mesh access state. No secrets or signed URLs are printed or committed.
2. Export the mobile detector before native build:
   `uv run --with coremltools==9.0 --with numpy==2.3.5 python tools/export_mobile_detector.py data/models/yolo11n.pt`.
   First run `make download-models` if weights are absent. The compiled 5.2 MB model
   stays git-ignored; export again on the build Mac rather than committing it.
3. In app: `npm ci`, `npx expo prebuild --platform ios`, then
   `npx expo run:ios --device` on a configured Mac with current Ruby/CocoaPods and
   Apple signing. Grant camera, microphone, speech recognition and motion access.
4. Start the existing server with `make server`. Enter its HTTP(S) address in the app.
   Spatial navigation remains stopped until a verified map and alignment are ready.
5. The legacy Expo Go camera demo remains available via `npx expo start`.

## Prepared-map contracts

All prepared property artifacts stay under git-ignored data/spatial.
`landmarks.npz` has finite Nx3 metric API points and Nx128 SIFT descriptors, one row
per registered image feature. The runtime never accepts arbitrary client-supplied
metric pose or a client's claimed alignment confidence.

`map.json` contains navigation (existing PropertyMap), verified landmarks with id,
label, x_m, y_m, floor, verified, and optional critical; floors keyed by floor ID with
camera_z_min/max; and validation with physical_pose_validated, position_error_m,
heading_error_deg, drift_m_per_second, drift_m_per_meter and
heading_drift_deg_per_second. The first native build sends floor ID `ground`.
Use model x/y as the floor plane and z as up; OpenCV-to-ARKit camera axes are
explicitly converted. Measurements must be in meters and matrices row-major.

## Physical acceptance still required

Verify a signed build on the target iPhone; measure pose/heading errors against
independent ground truth; review two destinations and their connecting corridor;
test obstacle misses, false alarms, microphone interruptions and stale speech;
measure camera-to-decision and camera-to-audible warning latency externally, including
thermal slowdown. Test loss/recovery, wrong turns, permission denial and backgrounding.
Then conduct supervised route trials and accessibility evaluation with blind and
low-vision participants. Passing simulated localization tests does not satisfy these
gates or establish independent-navigation reliability.
