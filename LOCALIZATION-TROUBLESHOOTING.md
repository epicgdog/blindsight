# Location remains uncertain

The existing localizer recognizes one configured scan point. Other downloaded scan
points are competing hypotheses that reject a wrong match; they are not additional
locations it can lock onto. Standing elsewhere in the same building can therefore
correctly remain UNCERTAIN or LOST. A successful panorama-render test does not prove
real-phone matching at another physical point.

This change enables Expo's image orientation processing (skipProcessing=false).
Previously, raw photos could arrive sideways and quality=0.3 was ignored. Processing
can add capture latency; measure it on the test phone. See Expo Camera documentation:
https://docs.expo.dev/versions/latest/sdk/camera/#camerapictureoptions

Update both the server checkout and the Expo bundle, then restart/reload both. Open
GET /api/localization while the phone streams. The response is a snapshot of the
latest processed frame; the viewer shows the same reason under the location badge.

- phone_connected=false or no location: no active stream; fix connectivity first.
- no_candidate_above_retrieval_threshold: retrieval is below calibration threshold;
  inspect orientation and calibration against real photos, not synthetic images alone.
- insufficient_geometric_inliers: not enough verified features; inspect actual scan
  position, lighting, phone motion, changed furniture, masking, and camera field of view.
- matches_not_spread_across_image: evidence is concentrated in a small image patch.
- another_scan_matches_better: the selected scan loses to a competing location.
- waiting_for_consistent_frames: matches exist but the temporal/heading checks have
  not agreed; hold the camera steadily toward architectural features.
- locked: current matching checks passed, still not a metric walking pose.

Diagnostics include selected_pano_id, frame_shape, best score/inliers/grid cells,
competing inliers, and applied thresholds. No images or credentials are returned by
the diagnostic endpoint. An unknown yaw is now displayed as unknown, not zero.

First test at the exact selected scan position shown by /api/pano. Confirm that the
viewer shows upright live frames. Capture real-phone positive and nearby negative
examples; validate HFOV and recalibrate with held-out phone images before changing
thresholds. Do not force LOCKED or lower thresholds merely to hide uncertainty.

The last external inspection of minky.tailb9ac0d.ts.net returned HTTP 502. The actual
live rejection reason cannot be verified until its FastAPI backend is reachable and
someone streams from the physical test location.
