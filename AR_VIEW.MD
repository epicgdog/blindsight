# AR view: the route drawn on the camera image

Goal: show the route as a blue path on the floor in the live camera view (like Google Maps
Live View), with the destination pin standing in the room. The 2D map stays as a small inset.

## Verdict

- **Looks better: yes**, clearly, for a demo and for a sighted or low-vision companion.
- **Easier: no.** It is native Swift work, each change needs an Xcode rebuild (no hot reload),
  and position errors that are invisible on a 2D map are obvious in AR (a path 1 m off, or
  drawn through a wall).
- **Still worth doing**, because the first half of the work (ARKit as the motion tracker) fixes
  problems the app has today, with or without the AR picture:
  - position updates 60 times a second on the phone, not about once a second from the server
  - facing direction from ARKit, no compass and no calibration
  - real camera intrinsics per frame (today the field of view is assumed to be 55 degrees)
  - frames come from the AR session at up to 5 per second (today the photo capture limits
    the app to about 0.8 per second)
- **Not for the blind user directly.** They cannot see it. What they get is better tracking,
  so better spoken guidance.

## What exists already

- `app/modules/blindsight-tracking/ios/BlindsightTrackingModule.swift`: an ARKit world tracking
  session. Every 0.2 s it emits a `tracking` event with the camera pose (4x4), intrinsics,
  tracking state, steps and a JPEG of the frame (640 px wide, sensor orientation).
- `app/NativeNavigation.js` + `server/spatial.py`: a teammate's separate path that uses it. It
  is behind `EXPO_PUBLIC_NATIVE_NAVIGATION=1`, has no camera view, and its alignment is gated
  on a validation flag that is off. This plan reuses the module, not that pipeline.
- Main pipeline (`localize.py`, `position.py`, `tracker.py`, `agent.py`, `entrance_nav.py`):
  place, metric position by PnP against the mesh, route, pins. Unchanged by this plan except
  where stated.
- The test phone is an iPhone 15 (not Pro): **no LiDAR**. So no depth map and no scene mesh
  from ARKit. Plane detection and world tracking work.

## The core problem: tying ARKit's frame to the map

ARKit tracks the phone in its own frame W: metres, gravity aligned, but with an arbitrary
origin and an arbitrary rotation about the vertical. The route is in the map frame M (the
Matterport mesh). To draw the route in the camera view we need the alignment A: W -> M.

Because both frames know which way is up, A has only 4 unknowns: one rotation about the
vertical and a 3D shift. That is much easier to estimate well than a full 6 DoF pose.

**Source of A: the server's position fixes.** Each frame sent to the server carries the ARKit
camera pose at capture. When the server solves a metric position for that frame (PnP, as
today), that is one pair: "ARKit says the camera was at p_w, the map says it was at p_m".

- With positions only, two fixes at least 2 m apart give the rotation. One fix plus its visual
  heading gives a first, rougher answer at once.
- Solve A by weighted least squares over a sliding window (last 20 fixes or 30 m walked),
  weights from each fix's sigma, with outliers dropped (a fix more than 1 m from the current
  fit, 3 in a row before the fit is restarted).
- The window slides because ARKit drifts slowly (about 1 % of distance walked).
- Capture time does not matter: the pair uses the ARKit pose of that same frame, so a slow
  server answer does not smear the fit.

**Where it runs:** on the server (`server/ar_align.py`, new, pure numpy, unit tested). The
result goes back in every frame result as
`agent.ar = {yaw_deg, t: [x, y, z], rms_m, fixes, spread_m}`.
The phone applies A to everything it draws, and blends from the old A to a new one over
0.5 s so the path does not jump.

**Quality gate:** the AR path is shown only when `fixes >= 3`, `spread_m >= 2` and
`rms_m <= 0.5`. Until then the screen says "Look around slowly" and shows the 2D map.

## Phases

### Phase 1: ARKit as the tracker (no AR picture yet) - about 1 day

Useful alone. The 2D map gets smooth and fast.

1. **Native module.**
   - Rotate the JPEG upright and size it to 720 px on the short side, as the server expects.
   - Add pitch and roll from the ARKit pose to the event (replaces the accelerometer maths).
   - Do not refuse to start on a phone with no depth and no bundled detector.
2. **Phone JS.** New capture source in `App.js`: when the native module is present, frames come
   from `tracking` events and go out through the existing websocket client, with the ARKit
   pose and intrinsics in the frame header. `CameraView` (expo-camera) is not mounted in this
   mode: ARKit owns the camera. Without the module, the app works as today.
3. **Protocol.** Frame header gains optional `arkit: {transform, intrinsics, tracking}`.
4. **Server.**
   - `ar_align.py`: the fit above.
   - Use the real intrinsics for PnP when present.
   - Frame result carries `agent.ar`.
5. **Phone map.** Position and facing of the dot = A applied to the live ARKit pose, at
   display rate. The lerp, the magnetometer compass and the "hold the last fix" logic are
   only fallbacks then.
6. **Guidance.** `on_imu` already re-checks guidance between frames; feed it the aligned
   ARKit position (sent at 10 Hz in the IMU message), so "turn now" is said on time.

Tests: alignment from synthetic walks (exact, noisy, with outliers, with drift); protocol
round trip; guidance progress from ARKit positions between fixes.

### Phase 2: the AR picture - about 1 to 2 days

1. **Native view.** An `ARSCNView` exposed from the same Expo module, driven by the same
   `ARSession`. It fills the screen behind the banner, buttons and bottom bar that exist now.
2. **Props from JS:** the alignment A, the route points (map metres), the destination point,
   the floor height.
3. **What is drawn:**
   - the route as a flat ribbon 0.5 m wide on the floor, blue with moving chevrons, only the
     next 10 m, fading out at the far end
   - a turn arrow standing at the next corner
   - the pin as an upright marker with its label and distance; an arrow at the screen edge
     when the pin is behind the person
4. **Floor height.** From the map (floor z through A), refined by ARKit horizontal plane
   detection, so the ribbon lies on the floor and does not float.
5. **Layout.** AR full screen, the 2D map as a round inset in a corner (tap to swap). The
   debug sheet and the pin flow stay as they are. Dropping a pin by tapping the floor in AR
   (ray cast to the floor plane, then A to map metres) comes for free here.
6. **Fallback.** Tracking limited, or the quality gate fails: hide the ribbon, show the 2D map.

### Phase 3: walls hide the path - about 1 day, optional

Without LiDAR the phone cannot tell that a wall is in front of the path, so the ribbon would
show through walls. Two measures:

1. Cheap, in phase 2 already: draw only up to the next corner plus a short stub.
2. Proper: load a simplified copy of the Matterport mesh into the scene as an invisible
   occluder (writes depth, no colour). The building then hides the path correctly. This needs
   the alignment good to about 0.3 m, or the hidden edges look wrong; so it is behind its own
   stricter gate.

## What changes for the other plans

- **Drop pin:** unchanged; AR adds "tap the floor to drop".
- **Obstacle path:** easier. ARKit gives the true camera height and pitch, so the distance to
  an object from its floor contact point is more accurate, and the object can be kept in the
  ARKit frame between frames.
- **Extra floor:** ARKit height change is a strong sign of a floor change (stairs).

## Risks

- **Bad alignment looks worse than no AR.** Hence the gate, the blend and the 2D fallback.
- **Position fixes must be metric.** A depends on PnP fixes; frames that only match a place
  (no PnP) do not help. In long plain corridors fixes are rare, and A rests on ARKit alone.
- **ARKit loses tracking** in the dark, on blank walls, or when the camera is covered. Then
  the state is "limited" and the picture must freeze, not guess.
- **Native iteration is slow.** Every Swift change is a rebuild and reinstall (about 2 to
  5 minutes), and it cannot be tested from this Mac without the phone.
- **iOS only.** Android would need ARCore and a second native view.
- **Battery and heat.** ARKit plus streaming frames warms the phone in about 15 minutes.
- **Two camera owners.** ARKit and expo-camera cannot run together; the app must pick one
  mode at start.

## Order

1. Phase 1, then test on site: is the dot smooth, does it stay inside the corridors, what is
   `rms_m` after a 30 m walk.
2. Phase 2 only if `rms_m` stays under about 0.5 m. If it does not, AR will not look good, and
   the work goes into localization first (field of view calibration, more PnP fixes).
3. Phase 3 for the demo, if there is time.
