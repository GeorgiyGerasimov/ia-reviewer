"""End-to-end: a graph compiled WITHOUT a checkpointer must run to
publish_report even when reviewers surface major/critical findings.

Background: `ExploitProposalAgent.run` calls `interrupt()` for any
high-confidence finding. Without a checkpointer the LangGraph runtime
can't pause + resume, so the astream loop terminates on the interrupt
sentinel and `publish_report` never runs — leaving the user with a
"Review complete" chat message and a 404 on `/reports/<thread_id>.md`.

Fix contract: when `build_review_graph(..., checkpointer=None)`, both
`ExploitProposalAgent` and `ReviewDecisionAgent` are constructed with
`interrupts_enabled=False`. They degrade gracefully — no `interrupt()`
call — and the graph runs end-to-end to publish_report.
"""

from langchain_core.messages import AIMessage

from src.agents.coordinator import CoordinatorAgent
from src.graph.state import AgentReview, ReviewRequest, ReviewState, ValidationVerdict
from tests.e2e.helpers import build_test_graph


class _AcceptValidator:
    async def run(self, state):
        return {"validation": ValidationVerdict(accepted=True, category="accepted", reason="ok")}


class _MajorFindingReviewer:
    """Returns a critical finding — the kind that normally triggers
    ExploitProposalAgent's interrupt() for human approval."""

    def __init__(self, role: str):
        self.role = role

    async def run(self, state):
        review = AgentReview(
            agent_name=f"Fake{self.role.title()}Reviewer",
            role=self.role,
            findings=[
                {
                    "file": "src/x.py",
                    "line": 12,
                    "issue": "SQLi via f-string in user query",
                    "severity": "critical",
                }
            ],
            summary="critical SQLi",
            severity="critical",
            passed=False,
        )
        return {"agent_reviews": [review]}


async def test_graph_without_checkpointer_completes_to_publish_even_with_major_findings(
    mocker, tmp_path
):
    """The whole point: no checkpointer + a critical finding used to leave
    the graph stuck at `interrupt()` with no way to resume. The agents must
    auto-skip interrupts when no checkpointer is wired and let publish run."""
    # Mock the LLM used by ExploitProposalAgent to return a high-confidence
    # draft, which (in the buggy code path) would trigger interrupt(). With
    # the fix, the agent must NOT call interrupt() because there's no
    # checkpointer.
    fake_llm = mocker.AsyncMock()
    fake_llm.ainvoke = mocker.AsyncMock(
        return_value=AIMessage(content='```json\n{"confidence": 9, "proposal": "drop a UNION SELECT into the param", "reasoning": "obvious"}\n```')
    )
    mocker.patch("src.agents.exploit_proposal.ModelFactory.get", return_value=fake_llm)

    github = mocker.AsyncMock()
    github.post_pr_comment.return_value = 1
    coordinator = CoordinatorAgent(github=github, reports_dir=tmp_path)
    graph = build_test_graph(
        coordinator=coordinator,
        validator=_AcceptValidator(),
        dependency=_MajorFindingReviewer("dependency"),
        injection=_MajorFindingReviewer("injection"),
        owasp=_MajorFindingReviewer("owasp"),
        checkpointer=None,  # <-- the key condition
    )

    request = ReviewRequest(
        mode="repo",
        repo_url="https://github.com/o/r",
        ref="main",
        author="bot",
    )
    initial = ReviewState(request=request, thread_id="tid-nochk")
    final = await graph.ainvoke(initial)

    # The graph must have produced a final_report and run publish_report.
    assert final.get("final_report"), "aggregate_results did not run (final_report empty)"
    report_path = tmp_path / "tid-nochk.md"
    assert report_path.exists(), (
        f"publish_report did not run / write report; dir contents = "
        f"{list(tmp_path.iterdir())!r}"
    )
    # Exploit proposals must have been "skipped" rather than awaiting a human.
    proposals = final.get("exploit_proposals") or []
    assert proposals, "expected at least one ExploitProposal entry"
    assert all(p.status.startswith("skipped") for p in proposals), (
        f"with no checkpointer, ALL findings must be skipped (no interrupt); "
        f"got statuses = {[p.status for p in proposals]!r}"
    )
