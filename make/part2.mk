# Part 2 targets (included by the top-level Makefile).
.PHONY: part2-echo part2-tunnel part2-test part2-app-test part2-video part2-slides

part2-echo:  ## stand-in server on :8000 (until Part 1's server lands)
	uv run python3 app/dev/echo_server.py

part2-tunnel:  ## cloudflared tunnel + Expo QR
	scripts/tunnel.sh

part2-test:  ## all Part 2 tests (python integration + node unit)
	uv run pytest tests/part2 -q
	node --test "app/test/unit/*.test.js" "server/viewer/test/*.test.js"

part2-app-test:
	cd app && npm test

part2-video:  ## render the ~60 s simulated backup video to slides/backup.mp4
	uv run python3 tools/render_backup_video.py --out slides/backup.mp4

part2-slides:
	uv run python3 slides/build_deck.py
