# Rabinowitz Hall scan verification

Confirmed API model: P3S6VUTP3qB, Princeton - Rabinowitz Hall, 66 scan positions.
User supplied this API ID corresponding to the previously shared public viewer.
Downloaded selected panorama az0h181td1h01x8x9gxhnp47a (scan 34) and 20 competitors.
Six-face layouts passed continuity checks; selected horizon visually inspected upright.
Built 72 selected-panorama reference views. No surveyed navigation edges are inferred
from scan proximity. The selected point has no verified spoken room label yet.

Full evaluation on 2026-11-14 used SIFT + classic-hog, 400 rendered positive and
400 rendered negative frames, split 600 calibration / 200 held-out test, plus
20 positive and 20 negative walkthroughs. These are SIMULATED images rendered
from the downloaded property panoramas; no real-phone accuracy is claimed.

- Held-out positives: 100/100 matched.
- Held-out negatives: 0/100 false matches.
- Heading error: median 0.05 degrees, p95 0.14 degrees on renders.
- Verification latency p95: 16 ms per candidate; runtime update rate 21.8 Hz.
- Positive walkthroughs: 20/20 locked within two seconds.
- Negative walkthroughs: 0/20 falsely locked.
- Stress with seven competing scan points withheld: 0/33 false frame matches,
  0/7 falsely locked walkthroughs. Small stress sample; not a real-world guarantee.
- All evaluator acceptance targets passed.

Combined regression tests: 177 passed, one skipped (FFmpeg video dependency).
Fetcher regression tests: seven passed. Phone endpoint/client tests: eight passed.
The test echo server now stores mock captures in temporary directories, preventing
test frames from contaminating real-property calibration; prior captures were moved
aside locally before the full run.

Credentials, images, signed URLs and generated data remain git-ignored. Reproduce
with make setup, p3-fetch, p3-refs, p3-synth, then calibrate using --matcher sift
--retriever classic-hog and run tools/eval_localization.py. Raw local results are
data/eval/results.json; calibration is data/calibration.json. Real-phone tracking,
metric alignment, surveyed routes and supervised walkthroughs remain outstanding.
