.PHONY: install test test-integration lint format run eval

install:
	python -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/python -m pytest

test-integration:
	.venv/bin/python -m pytest -m integration

lint:
	.venv/bin/ruff check . && .venv/bin/ruff format --check .

format:
	.venv/bin/ruff check --fix . && .venv/bin/ruff format .

run:
	.venv/bin/python -m app.cli serve

eval:
	.venv/bin/python scripts/run_eval.py
