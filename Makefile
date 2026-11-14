.DEFAULT_GOAL := help
UV ?= uv

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
	$(UV) run ruff check server tools/sim_client.py
	$(UV) run ruff format --check server tools/sim_client.py
	$(UV) run pytest

download-models:
	$(UV) run python -m server.detect --download

self-check:
	$(UV) run python -m server.server --self-check

demo-check:
	$(UV) run python tools/sim_client.py --demo-check $(ARGS)

soak:
	$(UV) run python tools/sim_client.py --soak $(ARGS)

-include make/part2.mk
-include make/part3.mk
