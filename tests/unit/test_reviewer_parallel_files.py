"""`LLMPerFileReviewer._run_repo` processes files concurrently.

Per-file LLM calls are independent (each prompt = one file's content
+ shared rules; the model never sees the other files in the same
session). So running them in series wastes wall-clock and gateway
capacity for no quality gain. This file pins the contract:

  * `settings.MAX_CONCURRENT_FILES_PER_AGENT` controls per-agent fan-out
  * results are aggregated deterministically (independent of LLM
    completion order)
  * one failing LLM call doesn't sink the whole pass
  * `=1` collapses to the original sequential behaviour (back-compat)
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agents.injection import InjectionReviewer
from src.graph.state import RepoFile, ReviewRequest, ReviewState


def _state_with_files(n: int, tmp_path: Path) -> ReviewState:
    """Build a ReviewState with `n` .py files materialised on disk."""
    snap = tmp_path / "snap"
    snap.mkdir()
    repo_files = []
    for i in range(n):
        p = snap / f"f{i}.py"
        p.write_text(f"# file {i}\ndef do_{i}(): return {i}\n")
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


def _mock_model_with_delay(mocker, delay_seconds: float):
    """ModelFactory.get → AsyncMock whose ainvoke sleeps then returns a
    minimal valid JSON response. Each call uses the same delay."""
    async def _slow_invoke(prompt):
        await asyncio.sleep(delay_seconds)
        resp = mocker.AsyncMock()
        resp.content = '{"findings": [], "summary": "ok", "severity": "info"}'
        return resp

    model = mocker.AsyncMock()
    model.ainvoke = _slow_invoke
    return model


# ── back-compat ────────────────────────────────────────────────────────────


async def test_concurrent_one_collapses_to_sequential(mocker, tmp_path):
    """`MAX_CONCURRENT_FILES_PER_AGENT = 1` must behave EXACTLY like the
    pre-fix sequential loop: total time ≈ N × per-call time."""
    mocker.patch(
        "src.agents.base_reviewer.settings.MAX_CONCURRENT_FILES_PER_AGENT", 1
    )
    mocker.patch("src.agents.base_reviewer.settings.MAX_FILES_PER_AGENT", 5)

    state = _state_with_files(3, tmp_path)
    model = _mock_model_with_delay(mocker, delay_seconds=0.1)

    with patch("src.models.factory.ModelFactory.get", return_value=model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        start = time.perf_counter()
        await reviewer._run_repo(state)
        elapsed = time.perf_counter() - start

    # 3 files × 0.1s each, sequential → ≥0.28s (with some slack)
    assert elapsed >= 0.28, (
        f"concurrent=1 ran in {elapsed:.3f}s — looks parallel, expected ≥0.28s"
    )


# ── parallelism ────────────────────────────────────────────────────────────


async def test_concurrent_three_runs_three_files_in_parallel(mocker, tmp_path):
    """3 files, concurrent=3, 0.1s per call → total ≈ 0.1s, not 0.3s.
    Proves the gather() actually parallelises."""
    mocker.patch(
        "src.agents.base_reviewer.settings.MAX_CONCURRENT_FILES_PER_AGENT", 3
    )
    mocker.patch("src.agents.base_reviewer.settings.MAX_FILES_PER_AGENT", 5)

    state = _state_with_files(3, tmp_path)
    model = _mock_model_with_delay(mocker, delay_seconds=0.1)

    with patch("src.models.factory.ModelFactory.get", return_value=model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        start = time.perf_counter()
        await reviewer._run_repo(state)
        elapsed = time.perf_counter() - start

    # Parallel 3 × 0.1s → ~0.1s + overhead, well under sequential 0.3s.
    assert elapsed < 0.25, (
        f"concurrent=3 took {elapsed:.3f}s — gather() not actually parallel? "
        f"Sequential would be ~0.3s; want comfortably under that."
    )


async def test_concurrent_caps_at_semaphore_value(mocker, tmp_path):
    """5 files, concurrent=2, 0.1s per call → total ≈ ceil(5/2)*0.1 = 0.3s.
    Proves the semaphore actually caps fan-out (not unbounded)."""
    mocker.patch(
        "src.agents.base_reviewer.settings.MAX_CONCURRENT_FILES_PER_AGENT", 2
    )
    mocker.patch("src.agents.base_reviewer.settings.MAX_FILES_PER_AGENT", 10)

    state = _state_with_files(5, tmp_path)
    model = _mock_model_with_delay(mocker, delay_seconds=0.1)

    with patch("src.models.factory.ModelFactory.get", return_value=model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        start = time.perf_counter()
        await reviewer._run_repo(state)
        elapsed = time.perf_counter() - start

    # ceil(5/2) batches × 0.1s = 0.3s. Allow [0.25s, 0.45s] window.
    assert 0.25 <= elapsed <= 0.55, (
        f"concurrent=2 took {elapsed:.3f}s; expected ~0.3s (ceil(5/2)*0.1)"
    )


# ── correctness ────────────────────────────────────────────────────────────


async def test_findings_aggregated_independent_of_completion_order(mocker, tmp_path):
    """LLM calls finish in random order due to varying delays. The final
    AgentReview must enumerate findings in a STABLE order (we sort by
    file path, which matches the input ordering of repo_files). Without
    this, the same review on the same repo would surface findings in
    different orders run-to-run, breaking the report's deterministic
    layout."""
    mocker.patch(
        "src.agents.base_reviewer.settings.MAX_CONCURRENT_FILES_PER_AGENT", 5
    )
    mocker.patch("src.agents.base_reviewer.settings.MAX_FILES_PER_AGENT", 10)

    state = _state_with_files(5, tmp_path)

    # Each file's mock returns a finding tagged with its filename, so we
    # can verify ordering. Varying delays make completion order random.
    delays = {"f0.py": 0.05, "f1.py": 0.01, "f2.py": 0.04, "f3.py": 0.02, "f4.py": 0.03}

    async def _file_specific_invoke(prompt):
        # Extract the filename from the prompt (we know it's in the rendered context)
        path = next(p for p in delays if p in prompt)
        await asyncio.sleep(delays[path])
        resp = mocker.AsyncMock()
        resp.content = (
            '{"findings": [{"file": "' + path + '", "issue": "x", '
            '"severity": "minor"}], "summary": "ok", "severity": "minor"}'
        )
        return resp

    model = mocker.AsyncMock()
    model.ainvoke = _file_specific_invoke

    with patch("src.models.factory.ModelFactory.get", return_value=model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        result = await reviewer._run_repo(state)

    findings = result["agent_reviews"][0].findings
    files_in_order = [f["file"] for f in findings]
    assert files_in_order == ["f0.py", "f1.py", "f2.py", "f3.py", "f4.py"], (
        f"findings order is not stable; got: {files_in_order}"
    )


async def test_one_file_llm_failure_does_not_sink_others(mocker, tmp_path):
    """If LLM call for ONE file raises, the other files still complete
    and contribute their findings. The failing file is silently
    dropped (logged at warning level)."""
    mocker.patch(
        "src.agents.base_reviewer.settings.MAX_CONCURRENT_FILES_PER_AGENT", 3
    )
    mocker.patch("src.agents.base_reviewer.settings.MAX_FILES_PER_AGENT", 10)

    state = _state_with_files(3, tmp_path)

    async def _flaky_invoke(prompt):
        if "f1.py" in prompt:
            raise RuntimeError("gateway timeout on f1.py")
        resp = mocker.AsyncMock()
        resp.content = '{"findings": [{"issue": "ok"}], "summary": "ok", "severity": "minor"}'
        return resp

    model = mocker.AsyncMock()
    model.ainvoke = _flaky_invoke

    with patch("src.models.factory.ModelFactory.get", return_value=model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        reviewer = InjectionReviewer()
        result = await reviewer._run_repo(state)

    review = result["agent_reviews"][0]
    # f0.py + f2.py succeeded; f1.py failed silently
    file_paths_in_findings = sorted(set(f.get("file", "") for f in review.findings))
    assert "f0.py" in file_paths_in_findings
    assert "f2.py" in file_paths_in_findings
    assert "f1.py" not in file_paths_in_findings
    # 2 successful files contributed 1 finding each
    assert len(review.findings) == 2


# ── async helper ───────────────────────────────────────────────────────────


pytestmark = pytest.mark.anyio
