# Similar-looking scan positions

Two implementations were reviewed: main's property localizer with labeled phone
photos, and feat/imu-position-tracking's multi-place matcher, pitch gate, Kalman
position filter and switch hysteresis. The IMU branch is not merged into main.
Changes are supplied on fix/disambiguate-scan-matches (main architecture) and
fix/imu-disambiguation (IMU architecture). Deploy the branch based on the server's
current architecture; do not replace the IMU branch wholesale with main.

## Visual identity, before smoothing

Raw inlier count can favor a repeated wall or pattern. The additional gate compares
geometrically verified LIVE image keypoints across credible rival locations. Matches
shared with a rival are not distinguishing evidence. Multiple views of the SAME
scan are not rivals. Nearby keypoints within two image pixels are treated as shared.

When another place has at least N_MIN verified inliers, the winner additionally
needs at least max(6, ceil(10% of its inlier count)) unique points, distributed across
at least two of the existing 3x3 image cells. Repeated points do not inflate this
count. Missing correspondence evidence fails closed. Existing inlier/grid/margin
and temporal checks still apply. This may increase uncertainty for genuinely
indistinguishable views; that is preferable to masking a wrong location with smoothing.

Main exposes distinctive_evidence with required/count/grid/passed and the reason
insufficient_distinguishing_features. On the IMU path the gate runs before position
fusion and is exposed in last_debug under distinctive_evidence. Existing Kalman,
motion/pitch and hysteresis checks are retained, not replaced or weakened.

Main's teaching photos now need the existing cross-place margin AND distinguishing
features. A tie no longer enrolls a potentially mislabeled photo. A folder with zero
accepted photos no longer renames a scan. Folder text alone is not visual evidence.

## Validation and limits

Regression tests include a raw-count winner supported only by repeated/shared image
features, distinctive features distributed across the image, one-patch evidence,
missing correspondences, same-place alternate views, multi-place recognition and
labeled-photo enrollment. Thresholds are conservative heuristics, NOT a measured
real-world accuracy guarantee. Existing held-out phone-photo tooling remains available.

For the actual confusing scan pairs, collect a separate test set and short walks in
both directions, including standing still and turning. Keep evaluation photos out of
the teaching directories. Compare wrong-place locks, switches while still, correct
reacquisition time, and unlocalized fraction before/after. Record both candidate
inliers and distinctive_evidence. More UNCERTAIN results alone do not prove better
accuracy. The fix cannot resolve a rival omitted from the candidate shortlist or
promise that all unshared features identify a unique physical place. Metric motion
constraints and multiple independent views remain complementary evidence.
