"""End-to-end: `state.thread_id` set on the initial state must survive
all the way through the graph to `publish`, where CoordinatorAgent uses
it as the filename for the on-disk report.

A 404 from `GET /reports/<thread_id>.md` in production told us this was
NOT happening: publish ran with an empty `thread_id` and (after the recent
defensive fix) silently skipped the disk write. The existing
`test_run_repo_review_*` tests fake `astream`, so they don't exercise
LangGraph's actual state propagation. This test does.
"""

from langgraph.checkpoint.memory import MemorySaver

from src.agents.coordinator import CoordinatorAgent
from src.graph.state import AgentReview, ReviewRequest, ReviewState, ValidationVerdict
from tests.e2e.helpers import build_test_graph


class _AcceptValidator:
    async def run(self, state: ReviewState) -> dict:
        return {"validation": ValidationVerdict(accepted=True, category="accepted", reason="test")}


class _FakeReviewer:
    def __init__(self, role: str):
        self.role = role

    async def run(self, state: ReviewState) -> dict:
        review = AgentReview(
            agent_name=f"Fake{self.role.title()}Reviewer",
            role=self.role,
            findings=[],
            summary=f"{self.role} ran",
            severity="info",
            passed=True,
        )
        return {"agent_reviews": [review]}


async def test_thread_id_survives_graph_and_publishes_report_to_disk(mocker, tmp_path):
    """Repo-mode review through the real graph: initial state has
    `thread_id="tid-prop"`. publish must write `<tmp_path>/tid-prop.md`."""
    github = mocker.AsyncMock()
    coordinator = CoordinatorAgent(github=github, reports_dir=tmp_path)
    graph = build_test_graph(
        coordinator=coordinator,
        validator=_AcceptValidator(),
        dependency=_FakeReviewer("dependency"),
        injection=_FakeReviewer("injection"),
        owasp=_FakeReviewer("owasp"),
        checkpointer=MemorySaver(),
    )

    request = ReviewRequest(
        mode="repo",
        repo_url="https://github.com/o/r",
        ref="main",
        author="bot",
    )
    initial = ReviewState(request=request, thread_id="tid-prop")

    final = await graph.ainvoke(
        initial,
        config={"configurable": {"thread_id": "tid-prop"}},
    )

    # The graph must NOT have lost our thread_id between nodes.
    assert final["thread_id"] == "tid-prop", (
        f"thread_id was lost in transit: final state = {final!r}"
    )
    # Publish must have written the report under tid-prop.md.
    report_path = tmp_path / "tid-prop.md"
    assert report_path.exists(), (
        f"publish failed to write report for thread_id 'tid-prop'; "
        f"reports dir contents = {list(tmp_path.iterdir())!r}"
    )
