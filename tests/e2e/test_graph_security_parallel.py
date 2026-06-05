"""End-to-end graph test: three security agents fan out in parallel,
aggregate sees all three reviews, publish posts a single comment."""

from langgraph.checkpoint.memory import MemorySaver

from src.agents.coordinator import CoordinatorAgent
from src.graph.state import AgentReview, ReviewState, ValidationVerdict
from tests.e2e.helpers import build_test_graph


class _AcceptValidator:
    """Stand-in validator that always accepts — keeps these tests focused on
    the parallel-fan-out contract, independent of the validator's own logic."""

    async def run(self, state: ReviewState) -> dict:
        return {"validation": ValidationVerdict(accepted=True, category="accepted", reason="test")}


class _FakeReviewer:
    """Stand-in for a security agent: returns one canned AgentReview when run."""

    def __init__(self, role: str, severity: str = "info"):
        self.role = role
        self.severity = severity
        self.calls = 0

    async def run(self, state: ReviewState) -> dict:
        self.calls += 1
        review = AgentReview(
            agent_name=f"Fake{self.role.title()}Reviewer",
            role=self.role,
            findings=[{"file": "x.py", "line": 1, "issue": f"{self.role} issue", "severity": self.severity}],
            summary=f"{self.role} ran",
            severity=self.severity,
            passed=self.severity not in ("critical", "major"),
        )
        return {"agent_reviews": [review]}


async def test_graph_fans_out_to_three_agents_and_publishes(mocker, pr_request):
    dep = _FakeReviewer("dependency", "minor")
    inj = _FakeReviewer("injection", "critical")
    owasp = _FakeReviewer("owasp", "info")

    github = mocker.AsyncMock()
    github.post_pr_comment.return_value = 777
    coordinator = CoordinatorAgent(github=github)

    graph = build_test_graph(
        coordinator=coordinator,
        validator=_AcceptValidator(),
        dependency=dep,
        injection=inj,
        owasp=owasp,
    )

    final = await graph.ainvoke(ReviewState(request=pr_request))

    assert dep.calls == 1
    assert inj.calls == 1
    assert owasp.calls == 1

    roles = {r.role for r in final["agent_reviews"]}
    assert roles == {"dependency", "injection", "owasp"}

    report = final["final_report"]
    assert "Dependencies — minor" in report
    assert "Injection — critical" in report
    assert "OWASP Top 10 — info" in report
    assert "**Overall severity:** critical" in report

    github.post_pr_comment.assert_awaited_once()
    assert final["pr_comment_id"] == 777
    assert final["completed"] is True


async def test_graph_persists_via_memory_checkpointer(mocker, pr_request):
    dep = _FakeReviewer("dependency")
    inj = _FakeReviewer("injection")
    owasp = _FakeReviewer("owasp")
    github = mocker.AsyncMock()
    github.post_pr_comment.return_value = 1

    saver = MemorySaver()
    graph = build_test_graph(
        coordinator=CoordinatorAgent(github=github),
        validator=_AcceptValidator(),
        dependency=dep,
        injection=inj,
        owasp=owasp,
        checkpointer=saver,
    )

    config = {"configurable": {"thread_id": "test-thread"}}
    await graph.ainvoke(ReviewState(request=pr_request), config=config)

    snapshot = graph.get_state(config)
    assert snapshot.values["completed"] is True
    assert len(snapshot.values["agent_reviews"]) == 3
