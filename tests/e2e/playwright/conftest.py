"""Playwright end-to-end tests for the ia-reviewer UI.

Spins up the real FastAPI app in a background thread (uvicorn) with
all external dependencies stubbed out, then drives a headless
Chromium against `http://localhost:<port>/`. No LLM calls go over
the network; no Postgres, no Langfuse, no real `git clone`.

Why a real app and not just a JSDOM unit test:

  Tier 1 (`tests/integration/test_ui_template_static_checks.py`) catches
  undefined CSS vars / non-unique ids / inline-script syntax errors —
  i.e. things you can spot by reading the template. Tier 3 covers the
  rest: did the workflow chart actually light up green when the graph
  finished? did the Stop button actually call /reviews/{id}/cancel and
  did the active-reviews panel disappear after?

Two scenarios live here today (the bare minimum recommended by the
"UI testing strategy" discussion):

  - `test_happy_path.py`     — submit → workflow circles light up →
                               Active reviews appears → final report renders
  - `test_stop_button.py`    — submit → Active reviews appears → click
                               Stop → confirm → panel disappears + chat
                               receives "Review cancelled by user."

Adding more scenarios: copy one of the files above and adjust the
assertions. The fixtures here scope a single app per test (`function`
scope) so cross-test pollution is impossible — Postgres is not wired
in this profile, so the cost is a few hundred ms per test.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import closing
from typing import Any
from unittest.mock import AsyncMock

import pytest
import uvicorn

# ── port allocation ───────────────────────────────────────────────────


def _free_port() -> int:
    """Bind-then-close trick to grab a port the kernel just gave us.
    Tiny race window vs. the next test that wants a port, but unique
    per-test fixture run is enough — Playwright tests aren't sharded
    on the same machine."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── stub LLM that the spawned app will use instead of a real gateway ──


class _StubChatResponse:
    """Mimics what `model.ainvoke(prompt).content` returns. The graph's
    response parsers strip a `<think>` trace + look for the first
    fenced JSON block — so we wrap our JSON in the canonical fence to
    stay close to the production shape."""

    def __init__(self, content: str) -> None:
        self.content = content


def _route_response(prompt: str, config: dict[str, Any]) -> str:
    """Pattern-match the agent role from the prompt and return the
    canonical JSON shape that role's `_parse_response` expects.

    `config` lets tests tweak individual responses:
      * `validator_accepted=False` (+ optional `validator_category`,
        `validator_reason`) → reject the request → graph routes to
        `notify_rejection` → reviewer circles go grey.
      * `reviewer_findings={"injection": [...]}` → bypass empty
        findings for one role (used by the markdown-table scenario
        to force a Summary table with non-zero counts).

    Keep this tiny — we're testing the UI, not the LLM.
    """
    lower = prompt.lower()
    # Validator decides accept/reject. The prompt has "trolling" and
    # specific category names; match either to find the validator call.
    if "is this a real review request" in lower or "trolling" in lower:
        if config.get("validator_accepted", True):
            return (
                '```json\n'
                '{"accepted": true, "category": "accepted", '
                '"reason": "playwright fixture"}\n'
                '```'
            )
        category = config.get("validator_category", "docs_only")
        reason = config.get("validator_reason", "playwright fixture reject")
        return (
            '```json\n'
            f'{{"accepted": false, "category": "{category}", '
            f'"reason": "{reason}"}}\n'
            '```'
        )
    # Review-decision agent checks ambiguity. No findings = no rerun.
    if "ambiguous" in lower or "needs_clarification" in lower:
        return '```json\n{"needs_clarification": false}\n```'
    # Reviewer findings — default empty, but `reviewer_findings` can
    # override per role for tests that need non-zero counts (e.g. the
    # markdown Summary-table scenario).
    overrides = config.get("reviewer_findings") or {}
    for role, findings_json in overrides.items():
        if role in lower:
            return (
                '```json\n'
                f'{{"findings": {findings_json}, '
                '"summary": "playwright stub", "severity": "minor"}\n'
                '```'
            )
    # Per-file reviewer prompts — Injection / OWASP / Configuration.
    # DependencyReviewer uses the script-first path so its summary LLM
    # only runs once at the end; we return the same minimal shape.
    return (
        '```json\n'
        '{"findings": [], "summary": "playwright stub", "severity": "info"}\n'
        '```'
    )


def _make_stub_model(config: dict[str, Any]) -> AsyncMock:
    """One AsyncMock per app — `.ainvoke(prompt)` dispatches via
    `_route_response(prompt, config)`. `config` is the test's
    parametrize payload (empty dict = defaults = happy path)."""
    model = AsyncMock()

    async def _ainvoke(prompt: str, *args: Any, **kwargs: Any) -> _StubChatResponse:
        return _StubChatResponse(_route_response(str(prompt), config))

    model.ainvoke = _ainvoke
    return model


# ── uvicorn app fixture ───────────────────────────────────────────────


@pytest.fixture
def playwright_app(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[str]:
    """Start a single FastAPI app on a free port with:

      * `ModelFactory.get` patched to a stub (no LLM network calls)
      * `DATABASE_URL=""` → no Postgres, in-memory checkpoint
      * `EMBEDDING_MODEL=""` → no RAG; PastContextAgent short-circuits
      * `LANGFUSE_PUBLIC_KEY` / `_SECRET_KEY` unset → no Langfuse
      * `clone_repo` patched to a no-op that returns an empty temp dir
        — happy-path test doesn't need a real repo on disk.

    Parametrize with `indirect=True` to tweak the stubs:

        @pytest.mark.parametrize(
            "playwright_app", [{"validator_accepted": False}], indirect=True
        )
        def test_reject(page, playwright_app): ...

    Supported keys: `validator_accepted`, `validator_category`,
    `validator_reason`, `reviewer_findings` (dict of role → JSON list).

    Yields the base URL string. Tears the server down after the test.
    """
    config: dict[str, Any] = getattr(request, "param", None) or {}
    from src.models.factory import ModelFactory

    # Env disables every external integration. `settings` is already
    # frozen at module import time (pydantic-settings caches values
    # from os.environ), so we ALSO patch the live settings object —
    # env-only would miss anything that was read at import.
    from src.utils.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", "")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    # WebSocket Origin check defaults to allow only `:8000`. We bind on
    # a random free port, so headless Chromium sends a different
    # Origin and the check would reject it. Empty list = check disabled
    # (the documented ops escape hatch).
    monkeypatch.setattr(settings, "WS_ALLOWED_ORIGINS", [])

    stub = _make_stub_model(config)
    monkeypatch.setattr(ModelFactory, "get", lambda *a, **kw: stub)
    # The factory caches model instances on `_instances` — flush so the
    # patch wins even if a prior test (or import-time) pre-populated it.
    ModelFactory._instances.clear()  # type: ignore[attr-defined]

    # Patch the snapshot path: real `clone_repo` calls `git` against a
    # remote URL — we don't want that in CI. Return a temp dir that
    # exists and has at least one file the file-classifier will see
    # (unless the test asks for an empty snapshot to trigger the
    # validator's `empty_repo` rejection branch).
    import tempfile
    from pathlib import Path as _Path

    fake_snapshot = _Path(tempfile.mkdtemp(prefix="pw-snap-"))
    if not config.get("empty_snapshot"):
        (fake_snapshot / "main.py").write_text("# stub for playwright\n")

    import src.integrations.repo_fetcher as _fetcher
    monkeypatch.setattr(_fetcher, "clone_repo", lambda *a, **kw: fake_snapshot)
    # `main.py` does `from src.integrations.repo_fetcher import clone_repo`
    # so we also need to patch the re-export in main's namespace.
    import main as _main
    monkeypatch.setattr(_main, "clone_repo", lambda *a, **kw: fake_snapshot)
    # cleanup_snapshot should not blow up on our fake dir — but the
    # default rm-rf handler works fine; nothing to patch.

    # Build the test app on a free port and run uvicorn in a background
    # thread. We can't use TestClient here — Playwright needs a real
    # listening socket.
    port = _free_port()
    config = uvicorn.Config(
        _main.app,  # the module-level FastAPI() instance
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    def _run() -> None:
        # uvicorn.Server.run() starts its own event loop. The patched
        # ModelFactory + env vars are picked up by the lifespan that
        # runs inside this thread.
        asyncio.run(server.serve())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    # Spin until the port answers /health.
    base_url = f"http://127.0.0.1:{port}"
    import urllib.error
    import urllib.request
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as r:
                if r.status == 200:
                    break
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    else:
        raise RuntimeError("playwright_app: uvicorn never accepted /health")

    yield base_url

    # Graceful shutdown — `should_exit` flag is uvicorn's documented
    # API. Give the thread a couple of seconds; if it doesn't exit,
    # the daemon flag ensures it dies with the test process.
    server.should_exit = True
    thread.join(timeout=3.0)
