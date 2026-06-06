"""`LLMPerFileReviewer._run_repo` emits per-file progress envelopes.

Contract:
  * On entry (after path filtering + cap), emits ONE `started_batch`
    event with `total`, `paths` (the full file list), and `role`.
  * After each per-file LLM call completes, emits ONE `file_done` event
    with `path`, `index` (1-based), `total`, and `findings_count`.
  * After the gather() completes, emits ONE `finished` event with
    `processed`, `failed`, `findings_total`, `role`.
  * Without an active emitter (back-compat), reviewer behaves exactly
    as PR #11 — no emits, no exceptions.
"""

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agents.injection import InjectionReviewer
from src.chat.hub import ChatHub
from src.chat.progress_emitter import ProgressEmitter, use_emitter
from src.chat.progress_store import ProgressStore
from src.graph.state import RepoFile, ReviewRequest, ReviewState


def _state_with_files(n: int, tmp_path: Path) -> ReviewState:
    snap = tmp_path / "snap"
    snap.mkdir()
    repo_files = []
    for i in range(n):
        p = snap / f"f{i}.py"
        p.write_text(f"def do_{i}(): return {i}\n")
        repo_files.append(RepoFile(path=f"f{i}.py", content="", size=p.stat().st_size))
    return ReviewState(
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            ref="main",
            snapshot_dir=str(snap),
            repo_files=repo_files,
        ),
        thread_id="tid",
    )


def _quick_mock_model(mocker):
    async def _invoke(prompt):
        resp = mocker.AsyncMock()
        resp.content = '{"findings": [], "summary": "ok", "severity": "info"}'
        return resp
    model = mocker.AsyncMock()
    model.ainvoke = _invoke
    return model


# ── back-compat (no emitter, current behaviour preserved) ──────────────────


async def test_no_emitter_no_side_effects(mocker, tmp_path):
    """When no emitter is bound, reviewer must not crash and must not
    emit anything (back-compat with PR #11 and earlier)."""
    state = _state_with_files(3, tmp_path)
    with patch("src.models.factory.ModelFactory.get",
               return_value=_quick_mock_model(mocker)):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        result = await reviewer._run_repo(state)
    # Still produces the AgentReview as before
    assert "agent_reviews" in result
    assert result["agent_reviews"][0].role == "injection"


# ── started_batch event ────────────────────────────────────────────────────


async def test_started_batch_envelope_emitted_first(mocker, tmp_path):
    """The first event must be `started_batch` with the full file list,
    so the UI can render the per-file progress block before any LLM
    call completes."""
    state = _state_with_files(3, tmp_path)
    store = ProgressStore()
    hub = ChatHub()
    emitter = ProgressEmitter("tid", store, hub)

    with patch("src.models.factory.ModelFactory.get",
               return_value=_quick_mock_model(mocker)):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        with use_emitter(emitter):
            await reviewer._run_repo(state)

    events = store.get("tid")
    file_progress = [e for e in events if e.get("type") == "file_progress"]
    assert file_progress, f"expected file_progress events; got {events}"
    first = file_progress[0]
    assert first["state"] == "started_batch"
    assert first["role"] == "injection"
    assert first["total"] == 3
    assert first["paths"] == ["f0.py", "f1.py", "f2.py"]


# ── file_done events ───────────────────────────────────────────────────────


async def test_one_file_done_event_per_file(mocker, tmp_path):
    """Each matched file produces a `file_done` event with index +
    total + findings_count + role."""
    state = _state_with_files(3, tmp_path)
    store = ProgressStore()
    emitter = ProgressEmitter("tid", store, ChatHub())

    with patch("src.models.factory.ModelFactory.get",
               return_value=_quick_mock_model(mocker)):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        with use_emitter(emitter):
            await reviewer._run_repo(state)

    file_done = [
        e for e in store.get("tid")
        if e.get("type") == "file_progress" and e.get("state") == "file_done"
    ]
    assert len(file_done) == 3
    paths = {e["path"] for e in file_done}
    assert paths == {"f0.py", "f1.py", "f2.py"}
    # All three carry the same `total` and a 1-based index
    for e in file_done:
        assert e["total"] == 3
        assert e["role"] == "injection"
        assert 1 <= e["index"] <= 3
        assert e["findings_count"] == 0  # mock returns 0 findings


# ── finished event ─────────────────────────────────────────────────────────


async def test_finished_event_emitted_last_with_totals(mocker, tmp_path):
    """The terminal `finished` event carries the aggregate numbers so
    the UI can switch the progress bar to its 'done' state without
    waiting for the workflow-level node_complete event."""
    state = _state_with_files(3, tmp_path)
    store = ProgressStore()
    emitter = ProgressEmitter("tid", store, ChatHub())

    with patch("src.models.factory.ModelFactory.get",
               return_value=_quick_mock_model(mocker)):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        with use_emitter(emitter):
            await reviewer._run_repo(state)

    finished = [
        e for e in store.get("tid")
        if e.get("type") == "file_progress" and e.get("state") == "finished"
    ]
    assert len(finished) == 1
    e = finished[0]
    assert e["role"] == "injection"
    assert e["processed"] == 3
    assert e["failed"] == 0
    assert e["findings_total"] == 0


# ── error path: one failing file still emits its file_done ─────────────────


async def test_failing_file_still_emits_file_done_with_failed_flag(mocker, tmp_path):
    """One failing per-file LLM call must still produce a `file_done`
    event so the UI doesn't show the file as stuck in-progress. The
    event carries `failed=True` so the UI can render it red."""
    state = _state_with_files(3, tmp_path)
    store = ProgressStore()
    emitter = ProgressEmitter("tid", store, ChatHub())

    async def _flaky(prompt):
        if "f1.py" in prompt:
            raise RuntimeError("upstream timeout")
        resp = mocker.AsyncMock()
        resp.content = '{"findings": [], "summary": "ok", "severity": "info"}'
        return resp

    model = mocker.AsyncMock()
    model.ainvoke = _flaky

    with patch("src.models.factory.ModelFactory.get", return_value=model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        with use_emitter(emitter):
            await reviewer._run_repo(state)

    file_done = [
        e for e in store.get("tid")
        if e.get("type") == "file_progress" and e.get("state") == "file_done"
    ]
    assert len(file_done) == 3
    f1 = next(e for e in file_done if e["path"] == "f1.py")
    assert f1.get("failed") is True
    others = [e for e in file_done if e["path"] != "f1.py"]
    for e in others:
        assert e.get("failed") is not True  # may be False or missing

    finished = [
        e for e in store.get("tid")
        if e.get("type") == "file_progress" and e.get("state") == "finished"
    ]
    assert finished[0]["processed"] == 2  # successes
    assert finished[0]["failed"] == 1


# ── empty/unreadable files: must NOT vanish from totals ───────────────────


async def test_empty_files_counted_in_terminal_envelope(mocker, tmp_path):
    """Reality check from production: a real repo has ~10 zero-byte
    `__init__.py` files. They get a `file_done` envelope (so the UI's
    counter ticks up live), but the LLM is intentionally skipped — the
    file body is empty. Before this test, the terminal `finished`
    envelope reported only `processed + failed`, which dropped the
    empties on the floor and made the UI flip from
    `54/54` (live) to `done (44/54)` (after `finished`). The status
    label said "done" while the bar showed an incomplete fraction.

    Contract: `processed + failed + skipped_empty == total`. The UI
    multiplexes these three buckets into one progress count so the bar
    always reads 54/54 on a clean run.
    """
    # 3 normal + 2 empty files. Empty files trigger the early-exit path
    # in `_read_snapshot_file` → `_process_one` returns None → must be
    # accounted for in `skipped_empty`, not silently dropped.
    snap = tmp_path / "snap"
    snap.mkdir()
    repo_files = []
    for i in range(3):
        p = snap / f"f{i}.py"
        p.write_text(f"def do_{i}(): return {i}\n")
        repo_files.append(RepoFile(path=f"f{i}.py", content="", size=p.stat().st_size))
    for j in range(2):
        p = snap / f"empty{j}.py"
        p.write_text("")  # 0-byte __init__.py-style file
        repo_files.append(RepoFile(path=f"empty{j}.py", content="", size=0))
    state = ReviewState(
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            ref="main",
            snapshot_dir=str(snap),
            repo_files=repo_files,
        ),
        thread_id="tid",
    )

    store = ProgressStore()
    emitter = ProgressEmitter("tid", store, ChatHub())

    with patch("src.models.factory.ModelFactory.get",
               return_value=_quick_mock_model(mocker)):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        with use_emitter(emitter):
            await reviewer._run_repo(state)

    # Every file (incl. empties) must still get a file_done envelope —
    # otherwise the live counter would stop at 3/5 mid-scan.
    file_done = [
        e for e in store.get("tid")
        if e.get("type") == "file_progress" and e.get("state") == "file_done"
    ]
    assert len(file_done) == 5

    # Terminal `finished` must surface all three buckets and they must
    # reconcile to `total`. UI sums them for the final progress label.
    finished = [
        e for e in store.get("tid")
        if e.get("type") == "file_progress" and e.get("state") == "finished"
    ]
    assert len(finished) == 1
    e = finished[0]
    assert e["total"] == 5
    assert e["processed"] == 3        # 3 non-empty files reached the LLM
    assert e["failed"] == 0
    assert e["skipped_empty"] == 2    # 2 zero-byte files — short-circuited
    assert e["processed"] + e["failed"] + e["skipped_empty"] == e["total"]


pytestmark = pytest.mark.anyio


# silence unused-import warning while still requiring the import (for fixture)
_ = asyncio
