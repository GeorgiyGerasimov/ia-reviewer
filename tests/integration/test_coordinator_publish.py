from src.agents.coordinator import CoordinatorAgent


async def test_publish_posts_report_and_marks_completed(pr_state, mocker, tmp_path):
    github = mocker.AsyncMock()
    github.post_pr_comment.return_value = 999
    pr_state.final_report = "## Security review\n\nlooks ok"

    # `reports_dir=tmp_path` keeps the disk-mirror side effect inside the
    # test sandbox; the default `Path("reports")` would leak files into
    # the project's real reports directory.
    coordinator = CoordinatorAgent(github=github, reports_dir=tmp_path)
    update = await coordinator.publish(pr_state)

    github.post_pr_comment.assert_awaited_once_with(pr_state.request.pr_url, pr_state.final_report)
    assert update["pr_comment_id"] == 999
    assert update["completed"] is True
    assert "error" not in update


async def test_publish_records_error_on_failure(pr_state, mocker, tmp_path):
    github = mocker.AsyncMock()
    github.post_pr_comment.side_effect = RuntimeError("403 forbidden")
    pr_state.final_report = "## Security review\n\nlooks ok"

    coordinator = CoordinatorAgent(github=github, reports_dir=tmp_path)
    update = await coordinator.publish(pr_state)

    assert update["completed"] is True
    assert "403 forbidden" in update["error"]


async def test_publish_skips_when_report_missing(pr_state, mocker, tmp_path):
    github = mocker.AsyncMock()
    coordinator = CoordinatorAgent(github=github, reports_dir=tmp_path)
    update = await coordinator.publish(pr_state)

    github.post_pr_comment.assert_not_called()
    assert update["completed"] is True
    assert "empty final_report" in update["error"]


async def test_pr_publish_also_writes_report_to_disk(pr_state, mocker, tmp_path):
    """PR-mode publish posts to GitHub AND mirrors the report to disk.

    The disk copy is what the UI report panel reads via `GET /reports/<id>.md` —
    we want the same UX in both modes. The GitHub comment is still the primary
    deliverable; the disk write is a UI-side artifact.
    """
    github = mocker.AsyncMock()
    github.post_pr_comment.return_value = 7777
    pr_state.thread_id = "t-pr"
    pr_state.final_report = "## Security review\n\npr-mode body"

    coordinator = CoordinatorAgent(github=github, reports_dir=tmp_path)
    update = await coordinator.publish(pr_state)

    github.post_pr_comment.assert_awaited_once_with(pr_state.request.pr_url, pr_state.final_report)
    report_path = tmp_path / "t-pr.md"
    assert report_path.exists(), "PR-mode publish must mirror the report to disk for the UI"
    assert report_path.read_text() == "## Security review\n\npr-mode body"
    assert update["pr_comment_id"] == 7777
    assert update["completed"] is True
    assert update.get("report_path") == str(report_path)
