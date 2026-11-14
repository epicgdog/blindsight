# IMU-path visual disambiguation

This branch extends feat/imu-position-tracking. It does not replace its Kalman
tracker, pitch checks, motion flags or scan-switch hysteresis. Before those stages
consume a matched multi-place frame, it now checks whether the winning place has
geometric image features that the plausible competing places cannot also explain.

Rivals with >=N_MIN verified inliers trigger the additional check. It requires
max(6, ceil(10% of winner inliers)) distinct live points across at least two 3x3
grid cells, after removing features within two pixels of rival inliers. Views of
the same place are excluded as rivals. Missing correspondence evidence fails closed.
last_debug.distinctive_evidence reports required/count/grid/passed. Existing
thresholds are unchanged. A rejected frame does not become a new tracker measurement.

These are uncalibrated conservative heuristics. They can increase uncertainty and
do not establish real-phone accuracy. Validate on held-out phone views of the
specific confused scan pairs and compare wrong locks, still-position jumps,
reacquisition delay and unlocalized time. Do not judge success by a steadier dot alone.

The main-architecture companion fix also tightens labeled-photo enrollment and
adds viewer diagnostics. It is on fix/disambiguate-scan-matches; use the branch
matching the deployed architecture. Do not merge the full main tree over this
branch as a way of obtaining this small visual-evidence check.
