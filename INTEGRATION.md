# Combined development baseline

This branch combines part-1, part_2 and part-3. The uv workspace retains the server
member and adds Part 2 QR and presentation dependencies. Both Python test trees
remain enabled. Run make setup, make check and make part2-test.

The selected property is https://my.matterport.com/models/6sd6A5r5EVp (Princeton –
Rabinowitz Hall). The fetcher defaults to this exact ID. Earlier Part 3 notes describe
testing P3S6VUTP3qB with its key; that is not evidence that the selected model is
accessible. Never silently replace the selected property.

Run `uv run python scripts/fetch_matterport.py --check` before downloading. API access
requires MATTERPORT_API_KEY and MATTERPORT_API_TOKEN in git-ignored .env.local.
Public browser access is insufficient for API downloads. Credentials are absent in
this integration checkout at initial verification. Real-property accuracy has therefore not been measured.

Subsequent authenticated check: the supplied key authenticates successfully to
HackPrinceton 2026, but model 6sd6A5r5EVp returns GraphQL not.found. Organizers must
grant this key access to the exact selected space or identify its authorized API ID.
The public viewer alone does not resolve this. Credentials are stored locally only.
The fetcher now uses a verified certifi CA bundle on macOS Python installs and
honors explicit SSL_CERT_FILE / SSL_CERT_DIR overrides; TLS verification stays enabled.

After access is granted: make p3-fetch, visually verify face orientation and text,
then make p3-refs, p3-synth, p3-calibrate and p3-eval. Property-specific data and
calibration are local artifacts, not committed assets. Start the real server using
LOCALIZER=real make server; use the Part 2 app against that server, not its echo
stand-in. See NAVIGATION.md for native pose and surveyed-route requirements.

Backup-video tests require Chrome, ffmpeg and ffprobe. Missing video tools are now
reported as a skip, not as a passing video render. The backup generator still uses
the echo server by default: it is a simulated presentation asset, not a live-vision
or localization acceptance test.

The QR launcher now uses the project's installed Python when uv is absent from
PATH. Secret-output regression tests use a synthetic environment token and never
read real credentials. No production credentials are required to run the tests.
