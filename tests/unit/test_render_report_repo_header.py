"""Final report header includes the review target (repo URL + ref, or PR URL).

The operator routinely opens multiple reports side by side; without an
explicit "Repository" / "Pull request" line in the rendered Markdown the
report is identifiable only by its `<thread_id>` filename, which is
useless when comparing findings across repositories.
"""

from src.agents.coordinator import CoordinatorAgent
from src.graph.state import AgentReview, RepoFile, ReviewRequest


def _review(role: str = "dependency") -> AgentReview:
    return AgentReview(
        agent_name=f"{role.title()}Reviewer",
        role=role,
        findings=[],
        summary="ok",
        severity="info",
        passed=True,
    )


def test_render_report_includes_repository_for_repo_mode(mocker):
    coordinator = CoordinatorAgent(github=mocker.AsyncMock())
    request = ReviewRequest(
        mode="repo",
        repo_url="https://github.com/sindresorhus/leven",
        ref="main",
        repo_files=[RepoFile(path="package.json", content="", size=10)],
        author="bot",
    )

    report = coordinator._render_report([_review()], request=request)

    assert "https://github.com/sindresorhus/leven" in report
    # Ref name belongs in the header so reports from `main` and a tag
    # can't be confused.
    assert "main" in report
    assert "Repository" in report


def test_render_report_includes_pr_url_for_pr_mode(mocker):
    coordinator = CoordinatorAgent(github=mocker.AsyncMock())
    request = ReviewRequest(
        mode="pr",
        pr_url="https://github.com/octo/repo/pull/42",
        diff="diff --git a/x b/x\n+1\n",
        files_changed=["x"],
        author="dev",
    )

    report = coordinator._render_report([_review()], request=request)

    assert "https://github.com/octo/repo/pull/42" in report
    assert "Pull request" in report
