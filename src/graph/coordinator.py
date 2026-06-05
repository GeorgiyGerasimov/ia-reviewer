from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agents.coordinator import CoordinatorAgent
from src.agents.dependency import DependencyReviewer
from src.agents.exploit_proposal import ExploitProposalAgent, route_after_proposal
from src.agents.injection import InjectionReviewer
from src.agents.owasp import OWASPTop10Reviewer
from src.agents.past_context import PastContextAgent
from src.agents.review_decision import ReviewDecisionAgent
from src.agents.validator import RequestValidator
from src.graph.state import ReviewState
from src.utils.node_timing import timed_node

_SECURITY_NODES = ("dependency_review", "injection_review", "owasp_review")


def route_after_validation(state: ReviewState) -> str:
    """Conditional-edge router after `validate_request`.

    - `accepted=True`  → `retrieve_past_context` (RAG step). That node
      itself hands off to all three security reviewers in parallel via a
      static edge, so the fan-out point moved one hop down the graph.
    - `accepted=False` → `notify_rejection`.
    - `validation is None` (safety net for a partially-implemented validator)
      → `notify_rejection`.
    """
    if state.validation is None or not state.validation.accepted:
        return "notify_rejection"
    return "retrieve_past_context"


def route_after_decision(state: ReviewState) -> list[str] | str:
    """Conditional-edge router after `review_decision`.

    - `extra_context` set (human approved a rerun) → fan back out to reviewers.
    - Otherwise (no ambiguity, cycle cap reached, or human said proceed)
      → `aggregate_results`.
    """
    if state.extra_context:
        return list(_SECURITY_NODES)
    return "aggregate_results"


def build_review_graph(
    coordinator: CoordinatorAgent | None = None,
    validator: RequestValidator | None = None,
    review_decision: ReviewDecisionAgent | None = None,
    dependency: DependencyReviewer | None = None,
    injection: InjectionReviewer | None = None,
    owasp: OWASPTop10Reviewer | None = None,
    exploit_proposal: ExploitProposalAgent | None = None,
    past_context: PastContextAgent | None = None,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    github=None,
) -> CompiledStateGraph:
    """Build the full security-review graph.

    Topology:
        START → validate_request
        validate_request ──cond──→ notify_rejection → END                  (reject)
                          ──cond──→ {dep, injection, owasp}_review         (accept, parallel)
                                            ↓ fan-in
                                    review_decision
                                            ↓ cond
                                            ├──→ {reviewers}   (rerun, cycle++)
                                            └──→ aggregate_results
                                                    ↓
                                            publish_report                  (report saved BEFORE any human Q&A)
                                                    ↓
                                            process_proposal                (Phase C — sequential, chat-only)
                                                    ↓ cond
                                                    ├──→ process_proposal   (more findings)
                                                    └──→ END

    Note the publish↔proposal ordering: the report is written to disk
    the moment aggregate finishes; exploit-proposal interrupts that
    follow can pause the graph forever without ever blocking the
    primary deliverable. The proposals collected after publishing live
    in `state.exploit_proposals` (chat / UI) but do NOT make it into
    the on-disk markdown — by design.
    """
    coordinator = coordinator or CoordinatorAgent()
    # Reviewers need a GitHubClient in repo-mode to call fetch_file per file.
    # Default to whatever the coordinator already holds so production wires
    # one client across all agents (test setups can pass their own).
    github = github or getattr(coordinator, "github", None)
    # `interrupt()` only works when LangGraph has a checkpointer to pause
    # against. With no checkpointer wired, surface that to the agents so
    # they can degrade gracefully (auto-skip the human step) instead of
    # stalling the run on a question that can never be answered.
    interrupts_enabled = checkpointer is not None
    validator = validator or RequestValidator()
    review_decision = review_decision or ReviewDecisionAgent(interrupts_enabled=interrupts_enabled)
    dependency = dependency or DependencyReviewer(github=github)
    injection = injection or InjectionReviewer(github=github)
    owasp = owasp or OWASPTop10Reviewer(github=github)
    exploit_proposal = exploit_proposal or ExploitProposalAgent(
        interrupts_enabled=interrupts_enabled,
    )
    # `PastContextAgent` is always added to the graph — when its embedder
    # or store is None, `run()` short-circuits to an empty update. Keeps
    # the topology stable across "RAG configured" / "RAG disabled" setups.
    past_context = past_context or PastContextAgent()

    graph = StateGraph(ReviewState)
    # Every node is wrapped in `timed_node(...)` so wall-clock durations
    # land in the structured app log (`graph.timing` logger) on the same
    # line shape:
    #   node_complete node=<n> thread_id=<id> duration_ms=<N> status=ok|error
    # See `docs/performance-and-cost.md` for the parsing recipes.
    graph.add_node("validate_request", timed_node("validate_request")(validator.run))
    graph.add_node("notify_rejection", timed_node("notify_rejection")(coordinator.notify_rejection))
    graph.add_node("retrieve_past_context", timed_node("retrieve_past_context")(past_context.run))
    graph.add_node("dependency_review", timed_node("dependency_review")(dependency.run))
    graph.add_node("injection_review", timed_node("injection_review")(injection.run))
    graph.add_node("owasp_review", timed_node("owasp_review")(owasp.run))
    graph.add_node("review_decision", timed_node("review_decision")(review_decision.run))
    graph.add_node("aggregate_results", timed_node("aggregate_results")(coordinator.aggregate))
    graph.add_node("process_proposal", timed_node("process_proposal")(exploit_proposal.run))
    graph.add_node("publish_report", timed_node("publish_report")(coordinator.publish))
    # Re-render the main `.md` once the exploit loop has populated
    # state.exploit_proposals, and write sibling `<tid>.exploit.<fid>.md`
    # files for every save_mode="file" approved entry. publish_report wrote
    # a pre-exploit snapshot of the report so it's durable even if the
    # user never answers the chat prompts; this node updates it once the
    # answers (or timeouts) have all landed.
    graph.add_node("finalize_report", timed_node("finalize_report")(coordinator.finalize_exploits))

    graph.add_edge(START, "validate_request")
    graph.add_conditional_edges(
        "validate_request",
        route_after_validation,
        ["retrieve_past_context", "notify_rejection"],
    )
    graph.add_edge("notify_rejection", END)

    # retrieve_past_context fans out to the three security reviewers in parallel.
    # The reviewers' `_build_context` reads `state.past_findings_by_role` that
    # this node just populated (or left empty when RAG is disabled).
    for node in _SECURITY_NODES:
        graph.add_edge("retrieve_past_context", node)

    for node in _SECURITY_NODES:
        graph.add_edge(node, "review_decision")

    graph.add_conditional_edges(
        "review_decision",
        route_after_decision,
        [*_SECURITY_NODES, "aggregate_results"],
    )

    # Publish FIRST — the rendered report is durable as soon as aggregate
    # produced it. The exploit branch runs afterwards (chat-only) and
    # cannot stall the report on a human prompt that may never get answered.
    graph.add_edge("aggregate_results", "publish_report")
    graph.add_edge("publish_report", "process_proposal")
    graph.add_conditional_edges(
        "process_proposal",
        route_after_proposal,
        ["process_proposal", "finalize_report"],
    )
    graph.add_edge("finalize_report", END)

    return graph.compile(checkpointer=checkpointer)
