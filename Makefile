# Frontier Radar. Run `make help` for targets.
SINCE ?= $(shell date -v-30d +%F 2>/dev/null || date -d '30 days ago' +%F)
UV := uv run
SHELL := /bin/bash

.PHONY: help setup migrate collect test lint typecheck check-dashes fixtures ci

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

setup: ## install Python 3.12 deps with uv and create the local database
	uv python install 3.12
	uv sync --all-extras
	$(MAKE) migrate

migrate: ## apply Alembic migrations (SQLite by default, DATABASE_URL for Postgres)
	$(UV) alembic upgrade head

collect: ## run every collector over the last 30 days (override SINCE=YYYY-MM-DD)
	for s in github arxiv hn usaspending sbir; do $(UV) radar collect $$s --since $(SINCE) || exit 1; done
	$(UV) radar collect edgar --since $(SINCE)

test: ## unit tests, leakage guard, style rules
	$(UV) pytest

lint: ## ruff
	$(UV) ruff check radar tests eval scripts

typecheck: ## mypy
	$(UV) mypy radar

check-dashes: ## house rule: no em dashes anywhere (also enforced by tests/test_style.py)
	@! grep -rn "$$(printf '\342\200\224')" --exclude-dir={node_modules,.venv,.git,fixtures} --exclude=Anti_Fund_Master_Brief.md . || (echo "em dash found" && exit 1)
	@echo "no em dashes"

fixtures: ## re-record API cassettes from live sources (network required)
	$(UV) python scripts/record_fixtures.py

ci: lint typecheck test check-dashes ## everything CI runs
