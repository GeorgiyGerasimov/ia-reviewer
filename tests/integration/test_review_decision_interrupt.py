"""B.2 — ReviewDecisionAgent exercises LangGraph's interrupt() machinery.

We compile a minimal graph (just the decision node) with a MemorySaver so the
checkpointer can hold the pause point, then drive it through ainvoke/Command
the way main.py will in production.
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.agents.review_decision import ReviewDecisionAgent
from src.graph.state import AgentReview, ReviewState


def _build_decision_only_graph():
    """Tiny graph: START → review_decision → END. Lets the test pause at the
    interrupt and resume it without dragging in the full pipeline."""
    agent = ReviewDecisionAgent()
    graph = StateGraph(ReviewState)
    graph.add_node("review_decision", agent.run)
    graph.add_edge(START, "review_decision")
    graph.add_edge("review_decision", END)
    return graph.compile(checkpointer=MemorySaver())


def _ambiguous_state() -> ReviewState:
    return ReviewState(
        agent_reviews=[
            AgentReview(
                agent_name="DependencyReviewer",
                role="dependency",
                findings=[],
                summary="something seems off but I cannot pinpoint",
                severity="major",
                passed=False,
            )
        ]
    )


async def test_decision_pauses_at_interrupt_on_ambiguous_reviews():
    graph = _build_decision_only_graph()
    config = {"configurable": {"thread_id": "tid-pause"}}

    await graph.ainvoke(_ambiguous_state(), config=config)

    snapshot = await graph.aget_state(config)
    pending = [intr for task in snapshot.tasks for intr in task.interrupts]
    assert len(pending) == 1
    payload = pending[0].value
    assert payload["kind"] == "review_clarification"
    assert payload["cycle"] == 1
    assert payload["ambiguous_roles"] == ["dependency"]
    assert "rerun" in payload["question"].lower()


async def test_decision_resume_with_rerun_sets_extra_context_and_cycle():
    graph = _build_decision_only_graph()
    config = {"configurable": {"thread_id": "tid-rerun"}}

    await graph.ainvoke(_ambiguous_state(), config=config)
    final = await graph.ainvoke(Command(resume="rerun: this file is a Bazel stub"), config=config)

    assert final["cycle_count"] == 1
    assert final["extra_context"] == "this file is a Bazel stub"
    assert "rerun" in final["last_human_answer"].lower()


async def test_decision_resume_with_proceed_clears_extra_context():
    graph = _build_decision_only_graph()
    config = {"configurable": {"thread_id": "tid-proceed"}}

    await graph.ainvoke(_ambiguous_state(), config=config)
    final = await graph.ainvoke(Command(resume="looks fine, proceed"), config=config)

    # extra_context must not leak — otherwise the router would loop back to reviewers.
    assert final["extra_context"] == ""
    assert final["cycle_count"] == 0  # unchanged; no rerun was approved
    assert final["last_human_answer"] == "looks fine, proceed"


async def test_decision_forces_proceed_when_cycle_cap_reached():
    """Even on still-ambiguous reviews, review_decision must NOT interrupt
    once cycle_count reaches the cap — otherwise the loop is unbounded."""
    graph = _build_decision_only_graph()
    config = {"configurable": {"thread_id": "tid-cap"}}

    capped_state = ReviewState(
        cycle_count=2,  # at the cap with MAX_CYCLES=3 (cycle = 2+1 = 3 → forced proceed)
        agent_reviews=[
            AgentReview(
                agent_name="DependencyReviewer",
                role="dependency",
                findings=[],
                summary="still ambiguous",
                severity="major",
                passed=False,
            )
        ],
    )
    final = await graph.ainvoke(capped_state, config=config)

    snapshot = await graph.aget_state(config)
    pending = [intr for task in snapshot.tasks for intr in task.interrupts]
    assert pending == [], "cycle cap must prevent another interrupt"
    assert final["extra_context"] == ""


async def test_decision_clean_reviews_skip_interrupt():
    """No ambiguity → node returns immediately, no pending interrupt."""
    graph = _build_decision_only_graph()
    config = {"configurable": {"thread_id": "tid-clean"}}

    clean_state = ReviewState(
        agent_reviews=[
            AgentReview(
                agent_name="DependencyReviewer",
                role="dependency",
                findings=[{"issue": "x"}],
                summary="found one issue",
                severity="minor",
                passed=True,
            )
        ]
    )
    final = await graph.ainvoke(clean_state, config=config)

    snapshot = await graph.aget_state(config)
    pending = [intr for task in snapshot.tasks for intr in task.interrupts]
    assert pending == []
    assert final["extra_context"] == ""
