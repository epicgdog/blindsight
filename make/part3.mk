# Part 3 (Matterport + localization) targets.
# Part 1's Makefile includes this file; it also runs standalone:
#   make -f make/part3.mk p3-test
#
# Python runner: a lightweight uv environment with just what the Part 3 tools need
# (numpy, OpenCV, Pillow, pytest, certifi). Deliberately NOT Part 1's server project:
# that one pins PyTorch, which has no Intel-Mac builds, and no Part 3 tool needs it.
# Without PyTorch the localizer uses SIFT and the weight-free retriever (it falls back
# automatically). Only p3-weights installs PyTorch/transformers/LightGlue.
# Override with P3_PY=python (or P3_PY="uv run python" for the server's environment).
P3_DEPS    := --with numpy --with opencv-python-headless --with pillow --with pytest --with certifi
P3_ML_DEPS := --with torch --with transformers --with "lightglue @ git+https://github.com/cvg/LightGlue.git"
P3_PY      ?= uv run --no-project $(P3_DEPS) python
P3_PY_ML   ?= uv run --no-project $(P3_DEPS) $(P3_ML_DEPS) python

.PHONY: p3-help p3-access p3-list p3-fetch p3-try p3-hfov p3-refs p3-synth p3-calibrate p3-eval p3-feed p3-video \
        p3-weights p3-test p3-selfcheck p3-all p3-check p3-places-test p3-server-lite p3-onnx-check

p3-help: ## list the Part 3 targets
	@grep -E '^p3-[a-z-]+:.*## ' $(lastword $(MAKEFILE_LIST)) | sed 's/:.*## /\t/'

p3-access: ## check the Matterport key works and can see the demo model (MODEL=... to try another)
	$(P3_PY) scripts/fetch_matterport.py --check $(if $(MODEL),--model $(MODEL))

p3-list: ## list every scan point with its room name and height (MODEL=...); downloads nothing
	$(P3_PY) scripts/fetch_matterport.py --list $(if $(MODEL),--model $(MODEL))

p3-fetch: ## pick the pano (or PANO=<id>), download 2k faces for it and its neighbours (LABEL="...")
	$(P3_PY) scripts/fetch_matterport.py $(if $(MODEL),--model $(MODEL)) $(if $(PANO),--pano-id $(PANO)) $(if $(LABEL),--label "$(LABEL)")

p3-refs: ## verify face layouts, write contact sheets, build the 72 reference views
	$(P3_PY) server/refs.py --all

p3-synth: ## simulated positives, hard negatives and walkthroughs -> data/synthetic
	$(P3_PY) tools/make_synthetic.py

p3-calibrate: ## T_HI, T_LO, N_MIN, PLACE_RATIO from the calib split -> data/calibration.json
	$(P3_PY) tools/calibrate.py

p3-eval: ## Phase 4 numbers on the held-out split, written into NOTES-part3.md
	$(P3_PY) tools/eval_localization.py --write-notes NOTES-part3.md

p3-feed: ## synthetic feed frames + frames.jsonl for the sim client -> data/synthetic/feed
	$(P3_PY) tools/synthetic_feed.py --out data/synthetic/feed

p3-video: ## synthetic feed as an mp4 (one full turn) for the backup video
	$(P3_PY) tools/synthetic_feed.py --video data/synthetic/feed.mp4 --seconds 36

p3-weights: ## download DINOv2 + SuperPoint/LightGlue weights and prove they load offline
	$(P3_PY_ML) server/localize.py --prefetch

p3-try: ## run the localizer on real photos: DIR=~/Desktop/at_spot (JPEG/PNG; iPhone at 1x, portrait)
	$(P3_PY) server/localize.py --try "$(DIR)"

p3-places-test: ## which scan each phone photo is recognised as: DIR=... (subfolders like 34_front_door; elsewhere/)
	$(P3_PY) -m server.property_localize --try "$(DIR)"

# The whole server without PyTorch (Intel Macs): YOLO through OpenCV from an ONNX export.
P3_SERVER_DEPS := $(P3_DEPS) --with fastapi --with "uvicorn[standard]" --with websockets --with pydantic --with anyio
p3-server-lite: ## the server without PyTorch (Intel Mac): needs data/models/yolo11n.onnx; RECORD=1 to save frames
	DETECTOR=onnx LOCALIZER=$${LOCALIZER:-real} LOCALIZATION_SCOPE=$${LOCALIZATION_SCOPE:-property} \
		uv run --no-project $(P3_SERVER_DEPS) python -m server.server

p3-onnx-check: ## the ONNX detector loads and runs (IMAGE=photo.jpg to list what it sees)
	$(P3_PY) -m server.detect_onnx $(if $(IMAGE),--image "$(IMAGE)")

p3-hfov: ## estimate the phone camera's horizontal FOV from photos taken at the scan point (DIR=...)
	$(P3_PY) tools/calibrate.py --estimate-hfov "$(DIR)"

p3-selfcheck: ## PASS/FAIL: localizer loads (pano, refs, models) and calibration exists
	$(P3_PY) server/localize.py --selfcheck

p3-test: ## Part 3 unit + pipeline tests (procedural room, no Matterport data needed)
	$(P3_PY) -m pytest -q server/tests/test_p3_state_machine.py server/tests/test_p3_geometry.py \
		server/tests/test_p3_refs.py server/tests/test_p3_localizer.py server/tests/test_p3_eval.py \
		server/tests/test_p3_fetch.py server/tests/test_p3_feed.py \
		server/tests/test_p3_photo_places.py

p3-all: p3-refs p3-synth p3-calibrate p3-eval ## everything after p3-fetch

p3-check: p3-test ## what `make check` should run for Part 3
