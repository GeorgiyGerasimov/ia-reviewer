"""End-to-end: a graph compiled WITHOUT a checkpointer must run to
publish_report even when reviewers surface critical findings.

Background: `ExploitProposalAgent.run` used to call `interrupt()` for any
high-confidence critical finding. Without a checkpointer the LangGraph
runtime can't pause + resume, so the astream loop terminated on the
interrupt sentinel and `publish_report` never ran — leaving the user
with a "Review complete" chat message and a 404 on `/reports/<id>.md`.

The exploit branch has since moved out of the graph entirely (on-demand
via `POST /reviews/{tid}/exploits/{fid}`), so there's no interrupt to
trip on regardless of checkpointer. The remaining interrupt source is
`ReviewDecisionAgent` (Phase B clarification), which is constructed
with `interrupts_enabled=False` when checkpointer is None.

This test pins the contract that the graph runs end-to-end even when
critical findings exist and no checkpointer is wired.
"""

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


async def test_graph_without_checkpointer_completes_to_publish_even_with_critical_findings(
    mocker, tmp_path
):
    """The whole point: no checkpointer + a critical finding used to leave
    the graph stuck at the old `interrupt()` with no way to resume. With
    exploit drafting moved out of the graph, this must just work."""
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
    # The exploit branch has been removed from the graph — no proposals
    # are produced server-side regardless of checkpointer state. They
    # are created on demand via POST /reviews/{tid}/exploits/{fid}.
    assert final.get("exploit_proposals") in (None, [])
