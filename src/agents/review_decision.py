"""Phase B — human-in-the-loop re-review decision node.

Sits between the reviewer fan-in and `aggregate_results`. Inspects the
reviews for ambiguity; on ambiguous results it pauses the graph via
LangGraph's `interrupt()` and waits for a human response delivered via the
chat WebSocket. The human can request another review pass with extra
context, or proceed to aggregation.

Bounded by `MAX_CYCLES` to prevent runaway loops.
"""

from langgraph.types import interrupt

from src.graph.state import AgentReview, ReviewState

MAX_CYCLES: int = 3  # initial pass + up to 2 human-approved reruns

_RERUN_KEYWORD = "rerun"

_QUESTION_TEMPLATE = (
    "Reviewer(s) {roles} flagged a concern but produced no concrete findings. "
    "Reply `rerun: <extra context>` to retry with clarification, or any other "
    "message to proceed to the final report. (cycle {cycle} of {max_cycles})"
)


def _needs_clarification(reviews: list[AgentReview]) -> bool:
    """Triggers a human prompt when a reviewer signals a problem (passed=False)
    but produces no concrete findings — classic 'I think something's off but
    can't pinpoint it' scenario."""
    return any(not r.passed and not r.findings for r in reviews)


def _ambiguous_roles(reviews: list[AgentReview]) -> list[str]:
    return [r.role for r in reviews if not r.passed and not r.findings]


def _parse_resume(answer) -> tuple[bool, str]:
    """Parse the human's resume payload.

    Returns (approved_rerun, extra_context). The convention is:
      - Text starting with 'rerun' (case-insensitive) → approve a re-review.
        Anything after 'rerun:' (or 'rerun ') is the extra context to splice
        into the next pass.
      - Anything else → proceed to aggregate.
    """
    if not isinstance(answer, str):
        return False, ""
    stripped = answer.strip()
    if not stripped.lower().startswith(_RERUN_KEYWORD):
        return False, ""
    remainder = stripped[len(_RERUN_KEYWORD):].lstrip(": ").strip()
    return True, remainder


class ReviewDecisionAgent:
    """Pure routing logic + an interrupt call — no LLM, no external state.

    `interrupts_enabled=False` skips the `interrupt()` step entirely, which
    is required when the graph is compiled without a checkpointer (no way
    to suspend + resume). The node still clears `extra_context` and falls
    through to `aggregate_results` — the human-rerun path is just unavailable.
    """

    def __init__(self, interrupts_enabled: bool = True):
        self.interrupts_enabled = interrupts_enabled

    async def run(self, state: ReviewState) -> dict:
        cycle = (state.cycle_count or 0) + 1  # this pass we're judging the output of

        # Always clear extra_context on entry: the rerun that produced these
        # reviews already consumed it, and leaving it set would keep routing
        # back to reviewers forever.
        base_update = {"extra_context": ""}

        if cycle >= MAX_CYCLES:
            return base_update

        reviews = state.agent_reviews
        if not _needs_clarification(reviews):
            return base_update

        # No checkpointer → no way to receive the human's reply. Proceed
        # to aggregate without pausing the graph.
        if not self.interrupts_enabled:
            return base_update

        question = _QUESTION_TEMPLATE.format(
            roles=", ".join(_ambiguous_roles(reviews)),
            cycle=cycle,
            max_cycles=MAX_CYCLES,
        )
        answer = interrupt(
            {
                "kind": "review_clarification",
                "question": question,
                "cycle": cycle,
                "ambiguous_roles": _ambiguous_roles(reviews),
            }
        )

        approved, context = _parse_resume(answer)
        if approved:
            return {
                "cycle_count": cycle,
                "extra_context": context,
                "last_human_answer": str(answer),
            }
        return {
            "extra_context": "",
            "last_human_answer": str(answer),
        }
