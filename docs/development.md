# Development

## Toolchain

- Python 3.11. Newer is fine; older won't work (the codebase uses PEP
  604 unions and other 3.10+ syntax).
- `git` on `PATH` — runtime dependency for repo-mode clone.
- Optional: Docker for the Postgres + Langfuse stack.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# fill in keys as needed (see docs/configuration.md)
```

All Python dependencies live in `.venv/` at the project root. **Never
install into system Python.** This is enforced as a project convention
in [`.claude/skills/venv-policy/SKILL.md`](../.claude/skills/venv-policy/SKILL.md);
the [`venv-policy`](../.claude/skills/venv-policy/SKILL.md) skill kicks
in for any pytest/ruff/mypy/python invocation.

Always run tools through `.venv`:

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python …
```

Or activate once per shell:

```bash
source .venv/bin/activate
pytest
```

## TDD workflow

[`tdd-workflow`](../.claude/skills/tdd-workflow/SKILL.md) is a hard
rule on this project. For every behavior change:

1. Write a failing test that captures the desired behavior.
2. Run it (`pytest -x`). Confirm it actually fails — that's how you
   know the test exercises the code you think it does.
3. Write the minimal implementation to make it pass.
4. Run it. Confirm it passes.
5. Refactor if needed, keeping the test green.

**Hard stop**: if the red→green cycle exceeds 10 iterations without
the test passing, stop. Report the gap (what the test expects vs. what
the code produces and why it's stuck) and decide: redesign the test,
redesign the implementation, or skip.

For non-trivial test sets, propose the tests in one sentence each
before writing them. Sign-off keeps the test-first contract honest.

## Testing principles

[`testing-principles`](../.claude/skills/testing-principles/SKILL.md)
spells out the rules; the high-level shape:

### Never make real API calls in tests

Mock at the boundary, not deep in the call stack.

| External | How |
|---|---|
| `httpx` | `respx` — mocks the transport layer; the test code still goes through the real httpx client. |
| LLM models | `mocker.patch("src.agents.<...>.ModelFactory.get", return_value=mocker.AsyncMock())` — controlled `.ainvoke().content`. |
| `subprocess.run` | `mocker.patch("src.integrations.repo_fetcher.subprocess.run", return_value=...)` — clone tests never shell out. |
| Filesystem | Use `tmp_path` (pytest builtin). Tests that touch `reports/` or `snapshots/` must point `CoordinatorAgent(reports_dir=tmp_path)` at the sandbox to avoid leaking files into the real project dir. |

### Layered tests

| Layer | Where | What |
|---|---|---|
| Unit | `tests/unit/` | Pure logic, zero I/O. Routing functions, parsers, block builders, `_parse_response`. |
| Integration | `tests/integration/` | One component, all external deps mocked. `agent.run()`, `client.fetch_pr()`, `coordinator.publish()`. Also Tier-1 UI static checks (`test_ui_template_*.py`). |
| E2E | `tests/e2e/` | Full LangGraph run; external I/O mocked. Validates contracts that span the whole graph (parallel fan-out, accept/reject paths, no-checkpointer degradation). |
| UI e2e | `tests/e2e/playwright/` | Tier-3 UI tests: headless Chromium against a real FastAPI app with every external dep stubbed (~7 s for all 5 scenarios). See [`docs/ui-testing.md`](ui-testing.md). |

### Fixtures live in `tests/fixtures/` as real files

`.diff` and `.json` files reflect real GitHub API responses; load them
via `conftest.py` helpers (`load_text`, `load_json`). Avoid inlining
large strings in test code.

## Running tests

```bash
.venv/bin/pytest                  # all (including Playwright if installed)
.venv/bin/pytest tests/unit       # fast feedback loop
.venv/bin/pytest tests/integration
.venv/bin/pytest tests/e2e
.venv/bin/pytest --ignore=tests/e2e/playwright   # everything except UI tier 3
.venv/bin/pytest -x -q            # stop on first failure, quiet
.venv/bin/pytest -k "exploit"     # filter by name substring
```

Or via Makefile:

```bash
make test-fast        # unit + integration + non-Playwright e2e (~10 s)
make playwright       # UI tier 3 only, with screenshot/video on failure
make test-all         # everything
```

Non-Playwright suite runs in ~10 s on an M-series Mac. Playwright
adds ~7 s for 5 scenarios. CI runs them in parallel jobs — see
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

## Linting

```bash
.venv/bin/ruff check .
.venv/bin/ruff check --fix .   # apply safe fixes
```

Config in `pyproject.toml` — line length 120, target `py311`.

## Project skills (`.claude/skills/`)

These are conventions enforced by the Claude harness. The TL;DR for each
lives in [CLAUDE.md](../CLAUDE.md); the rules themselves are in:

| Skill | When it kicks in |
|---|---|
| [`tdd-workflow`](../.claude/skills/tdd-workflow/SKILL.md) | Any "add feature", "implement", "fix bug" request. |
| [`testing-principles`](../.claude/skills/testing-principles/SKILL.md) | Writing or reviewing any test. |
| [`venv-policy`](../.claude/skills/venv-policy/SKILL.md) | Before any `pytest`/`ruff`/`mypy`/`python` invocation. |
| [`docs-in-refactor`](../.claude/skills/docs-in-refactor/SKILL.md) | At the end of any refactor — `README.md`, `docs/`, `CLAUDE.md`, `.env.example` get updated **in the same change** as the code. |

## Adding a new reviewer

The four security reviewers (Dependency, Injection, OWASP Top 10,
Configuration) all extend either
[`LLMPerFileReviewer`](../src/agents/base_reviewer.py) (one LLM call
per matched file — used by Injection / OWASP / Configuration) or
[`ScriptedScannerReviewer`](../src/agents/base_reviewer.py) (deterministic
scan + optional summary LLM — used by Dependency via OSV.dev). Pick
the strategy that fits before subclassing `BaseReviewer` directly.

A new reviewer is:

1. Subclass `LLMPerFileReviewer` or `ScriptedScannerReviewer`, set
   `role`, `description`, `prompt_template`, `PATH_PATTERNS`, and
   `SKIP_CATEGORIES`.
2. Add the role string to `ALLOWED_SCOPE_ROLES` in
   [`src/graph/state.py`](../src/graph/state.py).
3. Wire it into [`build_review_graph`](../src/graph/coordinator.py) —
   accept the agent in the signature, default-construct it, add a node
   for it, and include it in the `_SECURITY_NODES` tuple so the fan-out
   reaches it.
4. Add a render label in
   [`src/agents/report_renderer.py::_ROLE_LABELS`](../src/agents/report_renderer.py).
5. Update the **React UI** (`web/`):
   - `web/src/components/WorkflowDiagram.tsx` — add a `STEPS` entry
     with `level: "child"` and `branch: "accept"`; extend the
     `SECURITY_CHILDREN` tuple so the synthetic
     `security_reviewers` parent waits for the new role to terminate.
   - `web/src/lib/useReviewStream.ts` — add `{role}_review` to the
     `CASCADE_ACTIVE` source map and to `ACCEPT_FANOUT` so the
     pulsing-active animation cascades through it.
   - `web/src/components/FileProgressPanel.tsx` — extend
     `ROLE_ORDER` + `ROLE_LABELS` so the per-file scan block
     renders for the new role in repo mode.
   - The legacy Jinja template at `templates/index.html` is a
     debug-only fallback — extending it is optional, no production
     surface depends on it.
6. Update [`docs/repo-mode.md`](repo-mode.md) with the new whitelist
   and any boundary changes to existing reviewers.
7. Add fixtures + tests:
   - Unit test for the path filter (`tests/unit/test_reviewer_path_filters.py`).
   - Integration test for the agent's `_run_pr` (PR-mode) and `_run_repo`
     (repo-mode) behaviour.
   - Vitest tests for the React additions (extending the existing
     `WorkflowDiagram.test.tsx` / `FileProgressPanel.test.tsx` /
     `useReviewStream.test.tsx` fixtures with the new role).
   - Update backend e2e tests to include the new role in fan-out
     assertions.

## Schema changes

`ReviewState` and `ReviewRequest` are dataclasses. Add a new field with
a sensible default. Don't add a required positional argument — every
existing call site uses keyword construction.

For `agent_reviews` / `exploit_proposals`, the `Annotated[list[…], add]`
reducer is what makes parallel fan-out safe. Any new collection field
written by multiple nodes concurrently needs the same treatment.

## Releasing

There's no release process yet — this is single-user dev. When that
changes, look at the `pyproject.toml`'s `version` field and the
existing test corpus before tagging anything.
