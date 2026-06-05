"""Integration tests for CoordinatorAgent.publish in repo-mode.

In PR-mode `publish` posts a top-level comment to the GitHub PR via the
GitHubClient. In repo-mode there is no PR — instead the report is written
to `<reports_dir>/<thread_id>.md` and the GitHub client is left alone.

A separate `GET /reports/{thread_id}.md` endpoint (test in test_api_repo.py)
serves the file back to the operator.
"""

import pytest

from src.agents.coordinator import CoordinatorAgent
from src.graph.state import RepoFile, ReviewRequest, ReviewState


async def test_coordinator_publish_repo_mode_writes_file_and_skips_github_comment(tmp_path, mocker):
    github = mocker.AsyncMock()
    state = ReviewState(
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            ref="main",
            repo_files=[RepoFile(path="src/x.py", content="", size=10)],
            author="bot",
        ),
        thread_id="t-1",
        final_report="## Security review\n\nrepo-mode report body",
    )

    coordinator = CoordinatorAgent(github=github, reports_dir=tmp_path)
    update = await coordinator.publish(state)

    report_path = tmp_path / "t-1.md"
    assert report_path.exists(), "expected report file written to disk"
    assert report_path.read_text() == "## Security review\n\nrepo-mode report body"

    github.post_pr_comment.assert_not_called()
    assert update["completed"] is True
    assert "error" not in update
    # Report path is surfaced in the state update so the chat broadcast
    # downstream can reference it.
    assert update.get("report_path") == str(report_path)


@pytest.mark.parametrize("mode", ["repo", "pr"])
async def test_publish_skips_disk_write_when_thread_id_empty(tmp_path, mocker, mode):
    """Defensive: never default `thread_id` to a sentinel ("unknown") and
    write into the project's reports dir on its behalf. An empty
    `thread_id` means the orchestrator didn't stamp one — skip the write
    entirely so we can't clobber a real report named the same way."""
    github = mocker.AsyncMock()
    github.post_pr_comment.return_value = 1
    request_kwargs = (
        {"mode": "repo", "repo_url": "https://github.com/o/r", "ref": "main"}
        if mode == "repo"
        else {"mode": "pr", "pr_url": "https://github.com/o/r/pull/1", "diff": "d"}
    )
    state = ReviewState(
        request=ReviewRequest(**request_kwargs, author="bot"),
        thread_id="",  # ← the bug surface
        final_report="## Security review\n\nbody",
    )

    coordinator = CoordinatorAgent(github=github, reports_dir=tmp_path)
    await coordinator.publish(state)

    # No file in tmp_path: the empty thread_id short-circuited the write.
    assert list(tmp_path.iterdir()) == []
