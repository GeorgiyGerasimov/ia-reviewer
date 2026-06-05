"""Repo-mode: `DependencyReviewer` is script-first.

The reviewer must:
  1. Delegate to `DependencyScanner` — findings come from OSV, not the LLM.
  2. Call the LLM EXACTLY ONCE, only for a summary paragraph.
  3. Render severity / passed from the SCANNER, not from the LLM JSON.

Tests mock both `OSVClient.query` and the LLM at the boundary —
no real OSV / LLM calls.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.dependency import DependencyReviewer
from src.graph.state import RepoFile, ReviewRequest, ReviewState
from src.integrations.osv_client import Dep, Vuln


@pytest.fixture
def repo_request(tmp_path):
    """Repo-mode request with a single vulnerable npm lockfile on disk."""
    lockfile = tmp_path / "package-lock.json"
    lockfile.write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {},
                    "node_modules/lodash": {"version": "4.17.15"},
                },
            }
        )
    )
    return ReviewRequest(
        mode="repo",
        repo_url="https://github.com/o/r",
        ref="main",
        snapshot_dir=str(tmp_path),
        repo_files=[RepoFile(path="package-lock.json", content="", size=lockfile.stat().st_size)],
        author="bot",
    )


@patch("src.agents.dependency.OSVClient")
@patch("src.agents.base_reviewer.ModelFactory.get")
async def test_repo_mode_findings_come_from_osv_not_llm(
    mock_get_model, mock_osv_cls, repo_request
):
    """The LLM may write a wildly different summary text every run, but
    `findings` must reflect OSV's response 1:1."""
    osv_instance = AsyncMock()
    osv_instance.query.return_value = {
        Dep(name="lodash", ecosystem="npm", version="4.17.15"): [
            Vuln(
                id="GHSA-p6mc",
                aliases=["CVE-2020-8203"],
                summary="Prototype Pollution",
                severity="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:H/A:L 7.4",
                fixed_versions=["4.17.20"],
                references=["https://github.com/advisories/GHSA-p6mc"],
            ),
        ],
    }
    mock_osv_cls.return_value = osv_instance

    mock_model = AsyncMock()
    # Note: the LLM is asked for a free-form summary, NOT JSON. The
    # reviewer must not parse the LLM output for findings.
    mock_model.ainvoke.return_value = MagicMock(
        content="One npm package (lodash 4.17.15) has Prototype Pollution."
    )
    mock_get_model.return_value = mock_model

    agent = DependencyReviewer()
    state = ReviewState(request=repo_request)
    update = await agent.run(state)

    [review] = update["agent_reviews"]
    assert review.role == "dependency"
    # Findings: deterministic, from OSV
    assert len(review.findings) == 1
    f = review.findings[0]
    assert f["package"] == "lodash"
    assert f["cve"] == "CVE-2020-8203"
    assert "4.17.20" in f["fixed"]
    # Severity: from scanner's CVSS bucket, NOT the LLM
    assert review.severity == "major"
    assert review.passed is False
    # LLM was called exactly ONCE — only for the summary
    assert mock_model.ainvoke.await_count == 1
    # Summary text plumbed through to the review
    assert "lodash" in review.summary.lower() or "prototype" in review.summary.lower()


@patch("src.agents.dependency.OSVClient")
@patch("src.agents.base_reviewer.ModelFactory.get")
async def test_repo_mode_clean_repo_zero_llm_calls(
    mock_get_model, mock_osv_cls, repo_request
):
    """No findings → no need to ask the LLM to summarize nothing. Saves
    a token round-trip and keeps the report consistent ('No findings.')."""
    osv_instance = AsyncMock()
    osv_instance.query.return_value = {
        Dep(name="lodash", ecosystem="npm", version="4.17.15"): [],
    }
    mock_osv_cls.return_value = osv_instance

    mock_model = AsyncMock()
    mock_get_model.return_value = mock_model

    agent = DependencyReviewer()
    state = ReviewState(request=repo_request)
    update = await agent.run(state)

    [review] = update["agent_reviews"]
    assert review.findings == []
    assert review.severity == "info"
    assert review.passed is True
    # Zero LLM calls when there's nothing to summarise
    assert mock_model.ainvoke.await_count == 0


@patch("src.agents.dependency.OSVClient")
@patch("src.agents.base_reviewer.ModelFactory.get")
async def test_repo_mode_surfaces_unsupported_files_in_summary(
    mock_get_model, mock_osv_cls, tmp_path
):
    """When the snapshot has a manifest we can't parse yet (`go.sum`),
    the reviewer must disclose it — even when zero findings."""
    (tmp_path / "go.sum").write_text("example.com/foo v1.2.3 h1:deadbeef\n")
    request = ReviewRequest(
        mode="repo",
        repo_url="https://github.com/o/r",
        ref="main",
        snapshot_dir=str(tmp_path),
        repo_files=[RepoFile(path="go.sum", content="", size=10)],
        author="bot",
    )
    osv_instance = AsyncMock()
    osv_instance.query.return_value = {}
    mock_osv_cls.return_value = osv_instance
    mock_model = AsyncMock()
    mock_get_model.return_value = mock_model

    agent = DependencyReviewer()
    update = await agent.run(ReviewState(request=request))
    [review] = update["agent_reviews"]

    assert review.findings == []
    # The reviewer must mention go.sum was recognised but not scanned.
    assert "go.sum" in review.summary or "unsupported" in review.summary.lower()
    # No LLM call needed for this disclosure either.
    assert mock_model.ainvoke.await_count == 0


@patch("src.agents.dependency.OSVClient")
@patch("src.agents.base_reviewer.ModelFactory.get")
async def test_repo_mode_osv_failure_yields_empty_findings_and_note(
    mock_get_model, mock_osv_cls, repo_request
):
    """OSV is down → findings empty, summary explains why. Graph keeps
    going (`passed=True`) because we have no evidence of a problem."""
    osv_instance = AsyncMock()
    osv_instance.query.side_effect = RuntimeError("connection refused")
    mock_osv_cls.return_value = osv_instance
    mock_model = AsyncMock()
    mock_get_model.return_value = mock_model

    agent = DependencyReviewer()
    update = await agent.run(ReviewState(request=repo_request))
    [review] = update["agent_reviews"]

    assert review.findings == []
    assert review.passed is True
    assert "osv" in review.summary.lower()
    assert mock_model.ainvoke.await_count == 0
