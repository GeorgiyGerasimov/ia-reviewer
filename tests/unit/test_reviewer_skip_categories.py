"""`LLMPerFileReviewer` skips files based on per-role `SKIP_CATEGORIES`.

Each concrete reviewer declares which `FileCategory` values it should
NOT bother to scan (e.g. Injection skips TESTS/DOCS/INFRA). The skip
filter applies AFTER the existing PATH_PATTERNS filter — patterns
control "is this even our extension", categories control "is this our
KIND of file".

Contract:
  * Default `SKIP_CATEGORIES` on `LLMPerFileReviewer` is empty (full
    back-compat — subclasses opt in).
  * Files in a skipped category are removed from `matched` before LLM
    calls fire — no per-file LLM cost for them.
  * `started_batch` envelope carries the breakdown of skipped counts
    by category so the UI can render "scanning N, skipped: M tests /
    K docs as out-of-scope for {role}".
  * Skipped files still consume their cap slot? — No. The cap is
    `MAX_FILES_PER_AGENT` ON THE FILES WE ACTUALLY SCAN. Categories
    are applied first so the cap doesn't eat into our skip budget.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.agents.injection import InjectionReviewer
from src.agents.owasp import OWASPTop10Reviewer
from src.chat.progress_emitter import ProgressEmitter, use_emitter
from src.chat.progress_store import ProgressStore
from src.graph.state import RepoFile, ReviewRequest, ReviewState
from src.scanners.file_classifier import FileCategory


def _make_state(file_paths: list[str], tmp_path: Path) -> ReviewState:
    snap = tmp_path / "snap"
    snap.mkdir()
    repo_files = []
    for rel in file_paths:
        full = snap / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(f"# {rel}\nx = 1\n")
        repo_files.append(RepoFile(path=rel, content="", size=full.stat().st_size))
    return ReviewState(
        request=ReviewRequest(
            mode="repo",
            repo_url="x",
            ref="main",
            snapshot_dir=str(snap),
            repo_files=repo_files,
        ),
        thread_id="tid",
    )


def _mock_model(mocker):
    async def _invoke(prompt):
        resp = mocker.AsyncMock()
        resp.content = '{"findings": [], "summary": "ok", "severity": "info"}'
        return resp
    m = mocker.AsyncMock()
    m.ainvoke = _invoke
    return m


# ── Injection: skip TEST + DOCS + INFRA ───────────────────────────────


async def test_injection_skips_test_files(mocker, tmp_path):
    """Tests deliberately contain injection-pattern examples (`f-string`
    SQL in test_validator.py, `subprocess` invocations in test_fetcher,
    etc). Reviewing them produces noise. Skip."""
    state = _make_state([
        "src/api/users.py",
        "tests/unit/test_users.py",
        "tests/conftest.py",
    ], tmp_path)
    store = ProgressStore()
    emitter = ProgressEmitter("tid", store, mocker.AsyncMock())
    with patch("src.models.factory.ModelFactory.get", return_value=_mock_model(mocker)):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        with use_emitter(emitter):
            await reviewer._run_repo(state)
    # file_done events tell us what was actually scanned
    file_done_paths = {
        e["path"] for e in store.get("tid")
        if e.get("type") == "file_progress" and e.get("state") == "file_done"
    }
    assert "src/api/users.py" in file_done_paths
    assert "tests/unit/test_users.py" not in file_done_paths
    assert "tests/conftest.py" not in file_done_paths


async def test_injection_skips_docs(mocker, tmp_path):
    state = _make_state([
        "src/api/users.py",
        "docs/architecture.md",
        "README.md",
    ], tmp_path)
    store = ProgressStore()
    emitter = ProgressEmitter("tid", store, mocker.AsyncMock())
    with patch("src.models.factory.ModelFactory.get", return_value=_mock_model(mocker)):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        with use_emitter(emitter):
            await reviewer._run_repo(state)
    paths = {
        e["path"] for e in store.get("tid")
        if e.get("type") == "file_progress" and e.get("state") == "file_done"
    }
    # docs are filtered out by PATH_PATTERNS already (no .md / .py in docs/)
    # but the test guarantees they never sneak through if PATH_PATTERNS widens
    assert "src/api/users.py" in paths
    assert "docs/architecture.md" not in paths
    assert "README.md" not in paths


# ── OWASP: skip TEST + DOCS + VENDORED + GENERATED, keep INFRA ─────────


async def test_owasp_keeps_infra_files(mocker, tmp_path):
    """OWASP Top 10 covers A05 (misconfiguration) — Dockerfiles,
    docker-compose, k8s manifests are exactly where this lives."""
    state = _make_state([
        "src/middleware.py",
        "Dockerfile",
        "docker-compose.yml",
        "k8s/deployment.yaml",
    ], tmp_path)
    store = ProgressStore()
    emitter = ProgressEmitter("tid", store, mocker.AsyncMock())
    with patch("src.models.factory.ModelFactory.get", return_value=_mock_model(mocker)):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = OWASPTop10Reviewer()
        with use_emitter(emitter):
            await reviewer._run_repo(state)
    paths = {
        e["path"] for e in store.get("tid")
        if e.get("type") == "file_progress" and e.get("state") == "file_done"
    }
    assert "src/middleware.py" in paths
    assert "Dockerfile" in paths
    assert "docker-compose.yml" in paths
    assert "k8s/deployment.yaml" in paths


async def test_owasp_skips_test_and_docs(mocker, tmp_path):
    state = _make_state([
        "src/middleware.py",
        "tests/test_middleware.py",
        "docs/security-checklist.md",
    ], tmp_path)
    store = ProgressStore()
    emitter = ProgressEmitter("tid", store, mocker.AsyncMock())
    with patch("src.models.factory.ModelFactory.get", return_value=_mock_model(mocker)):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = OWASPTop10Reviewer()
        with use_emitter(emitter):
            await reviewer._run_repo(state)
    paths = {
        e["path"] for e in store.get("tid")
        if e.get("type") == "file_progress" and e.get("state") == "file_done"
    }
    assert "src/middleware.py" in paths
    assert "tests/test_middleware.py" not in paths


# ── started_batch breakdown ────────────────────────────────────────────


async def test_started_batch_carries_skipped_breakdown(mocker, tmp_path):
    """The UI needs to render 'scanning N, skipped: M tests / K docs'
    so the operator understands why the scan list looks shorter than
    the file tree. The started_batch envelope carries that count
    grouped by FileCategory.value."""
    state = _make_state([
        "src/a.py",
        "src/b.py",
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_c.py",
        "docs/intro.md",  # docs/ dir wins → docs even though .md
    ], tmp_path)
    store = ProgressStore()
    emitter = ProgressEmitter("tid", store, mocker.AsyncMock())
    with patch("src.models.factory.ModelFactory.get", return_value=_mock_model(mocker)):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        with use_emitter(emitter):
            await reviewer._run_repo(state)
    started = next(
        e for e in store.get("tid")
        if e.get("state") == "started_batch"
    )
    # injection PATH_PATTERNS includes *.py, so .md is filtered earlier
    # at pattern level — only py files reach the category filter.
    # Of the py files: 2 src/*.py = core (scanned), 3 tests/*.py = test (skipped).
    assert started["total"] == 2
    assert "skipped" in started
    # category value enum stringifies to "test" — check the count.
    assert started["skipped"].get(FileCategory.TEST.value) == 3


pytestmark = pytest.mark.anyio
