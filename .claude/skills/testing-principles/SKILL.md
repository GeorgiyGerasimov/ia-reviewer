---
name: testing-principles
description: Testing rules for ia-reviewer. Mocking strategy (respx, pytest-mock, ModelFactory.get patching), test layering (unit/integration/e2e), fixture conventions, and the "no real API calls ever" rule. Apply when writing or reviewing any test.
---

# Testing Principles

## Never make real API calls in tests

All external clients must be mocked at the boundary:

- `httpx` calls → mock with `respx`
- LLM models → patch `src.models.factory.ModelFactory.get` to return `AsyncMock` with controlled `.content`

**Why:** The agent interacts with GitHub and multiple LLM providers. Real calls make tests slow, flaky, require live credentials, and break in CI.

## E2E graph tests inject mocked agents — do not patch globally

LangGraph compiles the graph at build time with node references. Global patches on imported classes may not affect already-compiled nodes.

Use a `tests/e2e/helpers.py::build_test_graph()` helper that accepts agent overrides, or pass a mock coordinator directly into `build_review_graph(coordinator=mock)`.

## Test layers

Each layer has a different scope. A failing unit test is much cheaper to debug than a failing e2e test.

- **Unit** (`tests/unit/`) — pure logic, no I/O. Routing functions, URL parsers, comment formatters, `_parse_response`, severity normalization.
- **Integration** (`tests/integration/`) — one component with its dependencies mocked. `agent.run()`, `client.fetch_pr()`, coordinator publishing.
- **E2E** (`tests/e2e/`) — full LangGraph graph run or full FastAPI request, with all external I/O mocked.

## Fixtures live in `tests/fixtures/` as real-data files

`.diff` files and `.json` GitHub API responses are stored as files, not inline strings. Load them via `conftest.py` helpers (`load_text`, `load_json`).

**Why:** Realistic fixtures catch edge cases (encoding, special chars, large diffs). Inline strings in test code become unreadable fast.

## Settings stub helper

Use `tests.conftest.stub_integration_settings()` context manager when a test imports `GitHubClient` — it provides real-string defaults for settings used during HTTP-client init. A `MagicMock()` on settings breaks `httpx.AsyncClient(base_url=...)`.

## Tooling

- `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`)
- `pytest-mock` for `AsyncMock` / `MagicMock`
- `respx` for mocking `httpx.AsyncClient`
- `httpx` is already a prod dependency
- All run via `.venv/bin/pytest` (see venv-policy skill)
