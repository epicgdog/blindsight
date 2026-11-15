.DEFAULT_GOAL := help
UV ?= uv
CORE_FILES := server/__init__.py server/config.py server/protocol.py server/server.py \
 server/tests/test_full_simulation.py \
 server/spatial.py server/tests/test_spatial.py \
 server/disambiguation.py server/tests/test_disambiguation.py \
 server/property_localize.py server/tests/test_property_localize.py \
 server/localization_debug.py server/tests/test_localization_debug.py \
 server/navigation.py server/tests/test_navigation.py \
	server/cues.py server/detect.py server/record.py server/localize_stub.py tools/sim_client.py \
	server/tests/conftest.py server/tests/test_cues.py server/tests/test_detect.py \
	server/tests/test_protocol.py server/tests/test_record_and_checks.py server/tests/test_server.py

.PHONY: help setup server sim check self-check download-models demo-check soak
help:
	@echo "setup | server | sim ARGS='--images DIR' | check | download-models | self-check | demo-check | soak"

setup:
	$(UV) sync --locked

server:
	$(UV) run python -m server.server

sim:
	$(UV) run python tools/sim_client.py $(ARGS)

check:
	$(UV) run ruff check $(CORE_FILES)
	$(UV) run ruff format --check $(CORE_FILES)
	$(UV) run pytest
	node --test app/test/unit/*.test.js server/viewer/test/*.test.js

.PHONY: simulation
simulation:
	$(UV) run pytest server/tests/test_full_simulation.py server/tests/test_property_localize.py server/tests/test_disambiguation.py server/tests/test_spatial.py -q
	node --test app/test/unit/*.test.js server/viewer/test/*.test.js

download-models:
	$(UV) run python -m server.detect --download

self-check:
	$(UV) run python -m server.server --self-check

demo-check:
	@if [ -f tools/eval_localization.py ]; then $(UV) run python tools/eval_localization.py --no-stress; fi
	$(UV) run python tools/sim_client.py --demo-check $(ARGS)

soak:
	$(UV) run python tools/sim_client.py --soak $(ARGS)

-include make/part2.mk
-include make/part3.mk
