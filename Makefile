SHELL := /bin/bash
PY ?= python3

.PHONY: test lint fmt fmt-check build ci

test:
	$(PY) -m pytest tests/ -q

lint:
	$(PY) -m ruff check .

fmt:
	$(PY) -m ruff format .

fmt-check:
	$(PY) -m ruff format --check src tests

build:
	$(PY) -m build --no-isolation

ci: lint fmt-check test build
