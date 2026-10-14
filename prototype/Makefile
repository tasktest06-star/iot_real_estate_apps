# End-to-end prototype: clamp-on thermal leak sensor -> LoRaWAN -> detection -> insurer API
#
#   make venv      create .venv and install backend deps
#   make test      run every test suite (python + node)
#   make sim       generate a labelled synthetic fleet
#   make eval      score the detector against the P1 gate (<0.5 nuisance alerts/device-year)
#   make run       start the backend on :8000 (dashboard at /)
#   make e2e       full end-to-end: boot backend, stream a simulated fleet through it, assert
#   make clean     remove generated artefacts
#
# Deliberately NOT a target: building the firmware. There is no gcc, no
# arm-none-eabi-gcc, no cmake and no west on this box, so firmware/ ships as
# reviewable source with west instructions in firmware/README.md. Pretending
# otherwise would be the one genuinely dishonest thing this Makefile could do.

SHELL   := /bin/bash
PY      := ./.venv/bin/python
PYTEST  := ./.venv/bin/pytest
PORT    ?= 8000
DEVICES ?= 40
DAYS    ?= 30
SEED    ?= 7
FLEET   ?= build/fleet.jsonl

.PHONY: all venv test test-py test-node sim eval run e2e clean help

all: test

help:
	@sed -n '2,14p' Makefile | sed 's/^# \{0,1\}//'

venv: .venv/bin/python
.venv/bin/python:
	python3 -m venv .venv
	./.venv/bin/pip -q install --disable-pip-version-check -r requirements.txt
	@echo "venv ready"

test: test-py test-node

test-py: venv
	@echo "=== python: simulator, detection, backend ==="
	$(PYTEST) -q simulator detection backend

test-node:
	@echo "=== node: payload codec ==="
	cd codec && node --test

$(FLEET): venv
	@mkdir -p build
	$(PY) -m simulator --devices $(DEVICES) --days $(DAYS) --seed $(SEED) --out $(FLEET)

sim: $(FLEET)
	@wc -l < $(FLEET) | xargs printf "%s uplinks in $(FLEET)\n"

eval: $(FLEET)
	$(PY) -m detection.evaluate --input $(FLEET)

run: venv
	$(PY) -m uvicorn backend.app.main:app --host 127.0.0.1 --port $(PORT)

e2e: venv
	@bash scripts/e2e.sh

clean:
	rm -rf build *.db *.jsonl .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "cleaned (left .venv in place; rm -rf .venv to drop it)"
