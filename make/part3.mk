# Part 3 (Matterport + localization) targets.
# Part 1's Makefile includes this file; it also runs standalone:
#   make -f make/part3.mk p3-test
#
# Python runner: Part 1's uv project in server/ once it exists, otherwise an ephemeral
# uv environment with just what the Part 3 tools need. Override with P3_PY=python.
P3_DEPS    := --with numpy --with opencv-python-headless --with pillow --with pytest
P3_ML_DEPS := --with torch --with transformers --with "lightglue @ git+https://github.com/cvg/LightGlue.git"
P3_PY      ?= $(if $(wildcard server/pyproject.toml),uv run --project server python,uv run --no-project $(P3_DEPS) python)
P3_PY_ML   ?= $(if $(wildcard server/pyproject.toml),uv run --project server python,uv run --no-project $(P3_DEPS) $(P3_ML_DEPS) python)

.PHONY: p3-help p3-fetch p3-refs p3-synth p3-calibrate p3-eval p3-feed p3-video \
        p3-weights p3-test p3-selfcheck p3-all p3-check

p3-help: ## list the Part 3 targets
	@grep -E '^p3-[a-z-]+:.*## ' $(lastword $(MAKEFILE_LIST)) | sed 's/:.*## /\t/'

p3-fetch: ## Phase 3: panoLocations -> pick PANO_ID -> 2k faces for it and its neighbours
	$(P3_PY) scripts/fetch_matterport.py

p3-refs: ## verify face layouts, write contact sheets, build the 72 reference views
	$(P3_PY) server/refs.py --all

p3-synth: ## simulated positives, hard negatives and walkthroughs -> data/synthetic
	$(P3_PY) tools/make_synthetic.py

p3-calibrate: ## T_HI, T_LO, N_MIN, PLACE_RATIO from the calib split -> data/calibration.json
	$(P3_PY_ML) tools/calibrate.py

p3-eval: ## Phase 4 numbers on the held-out split, written into NOTES-part3.md
	$(P3_PY_ML) tools/eval_localization.py --write-notes NOTES-part3.md

p3-feed: ## synthetic feed frames + frames.jsonl for the sim client -> data/synthetic/feed
	$(P3_PY) tools/synthetic_feed.py --out data/synthetic/feed

p3-video: ## synthetic feed as an mp4 (one full turn) for the backup video
	$(P3_PY) tools/synthetic_feed.py --video data/synthetic/feed.mp4 --seconds 36

p3-weights: ## download DINOv2 + SuperPoint/LightGlue weights and prove they load offline
	$(P3_PY_ML) server/localize.py --prefetch

p3-selfcheck: ## PASS/FAIL: localizer loads (pano, refs, models) and calibration exists
	$(P3_PY_ML) server/localize.py --selfcheck

p3-test: ## Part 3 unit + pipeline tests (procedural room, no Matterport data needed)
	$(P3_PY) -m pytest -q server/tests/test_p3_state_machine.py server/tests/test_p3_geometry.py \
		server/tests/test_p3_refs.py server/tests/test_p3_localizer.py server/tests/test_p3_eval.py

p3-all: p3-refs p3-synth p3-calibrate p3-eval ## everything after p3-fetch

p3-check: p3-test ## what `make check` should run for Part 3
