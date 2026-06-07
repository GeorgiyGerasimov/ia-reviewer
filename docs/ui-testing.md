# UI testing — three tiers

The production UI is a React 18 + TypeScript SPA in `web/` (Vitest
suite covers it). The legacy Jinja template at `templates/index.html`
is retained as a debug fallback and has Tier 1 static guards. Three
tiers of testing cover the surface, from cheapest to most expensive.
Each tier catches a different class of bug.

## Tier 0 — Vitest (React component / hook unit tests)

**Where:** `web/src/**/*.test.{ts,tsx}` and `web/src/**/*.test.ts`.

**Cost:** ~3s for the full suite (~200 tests across ~22 files) via
`cd web && npm test`. Run on every contributor checkout; gated in CI.

**What it catches:**
- Reducer logic in `useReviewStream` (cascade-active, branch dimming,
  terminal coercion).
- HTTP hooks with retry-on-404 backoff (`useReview`,
  `useCriticalFindings`) — exhaust + race scenarios.
- WorkflowDiagram step set + status priority + PR-mode greyout.
- FileProgressPanel role ordering + skipped-categories summary.
- Markdown renderer XSS-safety + GFM table support.
- API client `APIError` shape + JSON parsing fallbacks.

This is the **primary** UI test surface — most regressions get
caught here before they reach the browser.

## Tier 1 — Static checks (legacy Jinja template only)

**Where:** `tests/integration/test_ui_template_static_checks.py` and
`tests/integration/test_ui_template_reviewers_sync.py`.

These tests guard the legacy `templates/index.html` fallback. The
React SPA is not exercised here — its equivalent guards live in
Tier 0 Vitest.

**What it catches:**
- Every `var(--xxx)` has a matching `--xxx:` declaration (caught
  `--radius-md`, `--text-primary`, `--text-secondary` already)
- All `id="…"` attributes are unique (no `getElementById` silent
  duplicate)
- Inline `<script>` blocks parse via `node --check` (typo / missing
  bracket detection without running JS)
- All four sidebar panels share `border-radius` / `background` /
  `padding` (regressions in visual uniformity)
- Every entry in `_SECURITY_NODES` appears in the workflow chart,
  the JS `SECURITY_NODES` const, `NEXT_AFTER`, `ACCEPT_PATH_NODES`,
  and `handleValidationResult`'s active-cascade (caught the
  Configuration-circle-stays-grey bug)

**Cost:** runs in ~50 ms as part of the main pytest job. No browser,
no node runtime (node is only used for `--check` and the test
auto-skips when it's missing on PATH).

## Tier 2 — Playwright e2e (headless Chromium against the real app)

**Where:** `tests/e2e/playwright/`.

**What it catches:**
- WebSocket pipe broken (Origin check too strict, envelope schema
  drift, JS handler dropped) → workflow circles don't light up
- Stop button doesn't actually call `/cancel` or the panel doesn't
  hide after
- Validator-reject UI flow paints `notify_rejection` and skips
  reviewer circles
- Theme toggle doesn't persist via `localStorage`
- Markdown Summary block renders as a real `<table>` (regression
  target from the wall-of-pipes bug)

**Cost:** ~7 s for all 5 scenarios locally, ~1 min in CI (browser
install dominates). Runs in a separate parallel CI job so a flake
doesn't block lint+pytest signal.

### Architecture

`tests/e2e/playwright/conftest.py` spins up `uvicorn` in a background
thread on a free port with every external dep stubbed:

| Dep | Stub strategy |
|---|---|
| LLM gateway | `ModelFactory.get` patched to an `AsyncMock` whose `ainvoke` dispatches via `_route_response(prompt, config)` — different scenarios pass different `config` dicts |
| Postgres | `settings.DATABASE_URL = ""` → in-memory checkpoint |
| Langfuse | env keys unset → tracing off |
| RAG / embedder | `settings.EMBEDDING_MODEL = ""` → `PastContextAgent` short-circuits |
| WebSocket Origin check | `settings.WS_ALLOWED_ORIGINS = []` (random port can't be in the default allowlist) |
| `git clone` | `clone_repo` patched to a tempdir with one `main.py` |

No Postgres, no Langfuse, no real `git`, no LLM. Tests are hermetic.

### Adding a new scenario

1. Copy one of the existing files in `tests/e2e/playwright/`.
2. If the scenario needs a tweaked stub, parametrize via
   `indirect=True`:

   ```python
   @pytest.mark.parametrize(
       "playwright_app",
       [{"validator_accepted": False}],
       indirect=True,
   )
   def test_my_scenario(page, playwright_app):
       ...
   ```

   Supported keys on the `config` dict:
   - `validator_accepted` (bool)
   - `validator_category` (str)
   - `validator_reason` (str)
   - `reviewer_findings` (`dict[role, json-str]` — JSON array of finding objects)
   - `empty_snapshot` (bool) — empty clone → `empty_repo` reject

3. Run locally with `make playwright`.
4. Don't forget `@pytest.mark.timeout(60)` on slow scenarios — the
   global default is 120 s but specifying explicitly makes the
   wall-clock budget obvious.

### Local setup

```bash
pip install -e ".[dev]"      # picks up pytest-playwright + plugins
make playwright-install      # one-time, ~90 MB Chromium download
make playwright              # run all UI scenarios with screenshot
                             # + video capture on failure
```

### Failure artifacts

`make playwright` passes `--screenshot=only-on-failure
--video=retain-on-failure` to the underlying pytest invocation. In
CI the same flags surface artifacts in the workflow run's "Summary"
tab when a scenario fails. Use them — they save a debugging round-
trip compared to inferring the bug from a stack trace.

## When to add a Playwright scenario

Good fits:
- The UI control of a new feature (a button, a dialog, a navigation
  flow). One scenario per control is fine.
- A bug that was found by a user screenshot. Add the scenario that
  would have caught it in CI.

Bad fits:
- Pure JS / TS logic without DOM side effects → unit-test it
  in Vitest (Tier 0)
  when we have it, or via in-template assertion (`document.querySelector`
  in dev console) for one-offs.
- Edge cases in the graph itself → `tests/e2e/` non-Playwright tests
  exercise the graph end-to-end without a browser.
- Performance / load → not here; runs in real browser, won't
  reproduce production timing.
