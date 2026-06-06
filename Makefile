# Developer convenience targets. All commands run inside the project
# venv — install once with `pip install -e ".[dev]"`.
#
# Tab-indented because Make requires it. If a target stops working
# with "missing separator", check it's a real tab, not 4 spaces.

PYTHON := .venv/bin/python
PYTEST := .venv/bin/pytest
RUFF   := .venv/bin/ruff

.PHONY: help test test-fast test-all playwright-install playwright lint fix docker-up docker-down

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-20s %s\n", $$1, $$2}'

test: test-fast  ## Alias for the fast lane (unit + integration + non-Playwright e2e)

test-fast:  ## Run everything that doesn't need a browser (~10s)
	$(PYTEST) -v --tb=short --ignore=tests/e2e/playwright

test-all: playwright  ## Run everything including Playwright UI tests (~30s + browser startup)
	$(PYTEST) -v --tb=short

playwright-install:  ## One-time: install headless Chromium (~90 MB)
	$(PYTHON) -m playwright install chromium

playwright:  ## Run only the Playwright UI suite. Assumes chromium is installed.
	$(PYTEST) tests/e2e/playwright/ -v --tb=short \
		--screenshot=only-on-failure \
		--video=retain-on-failure

lint:  ## ruff check (no auto-fix)
	$(RUFF) check src/ tests/ main.py benchmarks/

fix:  ## ruff auto-fix
	$(RUFF) check --fix src/ tests/ main.py benchmarks/

docker-up:  ## Bring up the full stack (app + postgres + langfuse)
	docker compose up -d --force-recreate app

docker-down:  ## Stop just the app (keep postgres + langfuse warm)
	docker compose stop app
