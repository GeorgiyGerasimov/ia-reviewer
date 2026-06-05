"""A.4 — full graph runs through accept and reject paths.

Each test substitutes the agents at the boundary so neither real LLMs nor
real GitHub are touched. The accept-path test reuses the parallel-fan-out
contract; the reject-path test verifies that no reviewer ran and the
rejection comment was posted with the right template.
"""

from src.agents.coordinator import CoordinatorAgent
from src.graph.state import AgentReview, ReviewRequest, ReviewState, ValidationVerdict
from tests.e2e.helpers import build_test_graph


class _StubValidator:
    def __init__(self, accepted: bool, category: str = "accepted", reason: str = ""):
        self._verdict = ValidationVerdict(accepted=accepted, category=category, reason=reason)
        self.calls = 0

    async def run(self, state: ReviewState) -> dict:
        self.calls += 1
        return {"validation": self._verdict}


class _FakeReviewer:
    def __init__(self, role: str, severity: str = "info"):
        self.role = role
        self.severity = severity
        self.calls = 0

    async def run(self, state: ReviewState) -> dict:
        self.calls += 1
        return {
            "agent_reviews": [
                AgentReview(
                    agent_name=f"Fake{self.role.title()}Reviewer",
                    role=self.role,
                    findings=[],
                    summary=f"{self.role} ran",
                    severity=self.severity,
                    passed=True,
                )
            ]
        }


def _request() -> ReviewRequest:
    return ReviewRequest(
        pr_url="https://github.com/o/r/pull/1",
        diff="diff --git a/x.py b/x.py\n+def f(): pass\n",
        files_changed=["src/x.py"],
        author="dev",
    )


async def test_accept_path_runs_three_reviewers_and_publishes(mocker):
    dep = _FakeReviewer("dependency")
    inj = _FakeReviewer("injection")
    owasp = _FakeReviewer("owasp")
    validator = _StubValidator(accepted=True, category="accepted")

    github = mocker.AsyncMock()
    github.post_pr_comment.return_value = 777
    coordinator = CoordinatorAgent(github=github)

    graph = build_test_graph(
        coordinator=coordinator,
        validator=validator,
        dependency=dep,
        injection=inj,
        owasp=owasp,
    )

    final = await graph.ainvoke(ReviewState(request=_request()))

    assert validator.calls == 1
    assert dep.calls == 1 and inj.calls == 1 and owasp.calls == 1
    assert {r.role for r in final["agent_reviews"]} == {"dependency", "injection", "owasp"}
    github.post_pr_comment.assert_awaited_once()
    body = github.post_pr_comment.await_args.args[1]
    assert "Security review" in body
    assert "Security review skipped" not in body  # accept path uses publish, not notify_rejection
    assert final["pr_comment_id"] == 777
    assert final["completed"] is True


async def test_reject_path_skips_reviewers_and_posts_rejection(mocker):
    dep = _FakeReviewer("dependency")
    inj = _FakeReviewer("injection")
    owasp = _FakeReviewer("owasp")
    validator = _StubValidator(accepted=False, category="docs_only", reason="docs only")

    github = mocker.AsyncMock()
    github.post_pr_comment.return_value = 42
    coordinator = CoordinatorAgent(github=github)

    graph = build_test_graph(
        coordinator=coordinator,
        validator=validator,
        dependency=dep,
        injection=inj,
        owasp=owasp,
    )

    final = await graph.ainvoke(ReviewState(request=_request()))

    assert validator.calls == 1
    assert dep.calls == 0 and inj.calls == 0 and owasp.calls == 0
    assert final["agent_reviews"] == []
    github.post_pr_comment.assert_awaited_once()
    body = github.post_pr_comment.await_args.args[1]
    assert "skipped" in body.lower()
    assert "documentation" in body.lower()
    assert final["pr_comment_id"] == 42
    assert final["completed"] is True
