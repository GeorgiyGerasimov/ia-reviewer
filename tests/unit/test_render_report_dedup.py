"""B.4 — CoordinatorAgent._render_report dedupes agent_reviews by role.

Across re-review cycles `agent_reviews` accumulates one entry per role per
pass. The final report must surface only the latest review per role (the
second pass supersedes the first). Order of the original list determines
'latest' — last-write-wins on the role key.
"""

from src.agents.coordinator import CoordinatorAgent
from src.graph.state import AgentReview


def _review(role: str, summary: str, severity: str = "info", *, passed: bool = True) -> AgentReview:
    return AgentReview(
        agent_name=f"{role.title()}Reviewer",
        role=role,
        findings=[],
        summary=summary,
        severity=severity,
        passed=passed,
    )


def test_render_report_keeps_only_latest_review_per_role(mocker):
    coordinator = CoordinatorAgent(github=mocker.AsyncMock())
    reviews = [
        _review("dependency", "first pass", severity="major"),
        _review("injection", "first pass", severity="critical"),
        _review("dependency", "second pass after clarification", severity="info"),
    ]
    report = coordinator._render_report(reviews)

    # The first-pass dependency summary must not appear; the second one must.
    assert "first pass" not in report or report.count("first pass") == 1
    assert "second pass after clarification" in report
    # Should be exactly one Dependencies section, not two.
    assert report.count("### Dependencies") == 1


def test_render_report_unique_roles_pass_through_unchanged(mocker):
    coordinator = CoordinatorAgent(github=mocker.AsyncMock())
    reviews = [
        _review("dependency", "deps ok", severity="info"),
        _review("injection", "inj ok", severity="info"),
        _review("owasp", "owasp ok", severity="info"),
    ]
    report = coordinator._render_report(reviews)
    assert "### Dependencies" in report
    assert "### Injection" in report
    assert "### OWASP Top 10" in report
