from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agents.configuration import ConfigurationReviewer
from src.agents.coordinator import CoordinatorAgent
from src.agents.dependency import DependencyReviewer
from src.agents.injection import InjectionReviewer
from src.agents.owasp import OWASPTop10Reviewer
from src.agents.past_context import PastContextAgent
from src.agents.report_formatter import ReportFormatter
from src.agents.review_decision import ReviewDecisionAgent
from src.agents.validator import RequestValidator
from src.graph.state import ReviewState
from src.utils.node_timing import timed_node

# Four parallel specialists. Order here is the fan-out order in the
# graph (and the column order in the Summary table — see
# `report_renderer._render_summary_table`).
_SECURITY_NODES = (
    "dependency_review",
    "injection_review",
    "owasp_review",
    "configuration_review",
)


def route_after_validation(state: ReviewState) -> str:
    """Conditional-edge router after `validate_request`.

    - `accepted=True`  → `retrieve_past_context` (RAG step). That node
      itself hands off to all four security reviewers in parallel via a
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
    configuration: ConfigurationReviewer | None = None,
    past_context: PastContextAgent | None = None,
    report_formatter: ReportFormatter | None = None,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    github=None,
) -> CompiledStateGraph:
    """Build the full security-review graph.

    Topology:
        START → validate_request
        validate_request ──cond──→ notify_rejection → END                          (reject)
                          ──cond──→ retrieve_past_context                          (accept)
                                              ↓
                                    {dep, injection, owasp, configuration}_review  (parallel fan-out)
                                              ↓ fan-in
                                      review_decision
                                              ↓ cond
                                              ├──→ {reviewers}   (rerun, cycle++)
                                              └──→ aggregate_results
                                                          ↓
                                                  format_report
                                                          ↓
                                                  publish_report → END

    Exploit generation is NOT in the graph anymore — it runs on demand
    via `POST /reviews/{tid}/exploits/{fid}` after the report is
    published. See `src/agents/exploit_proposal.py` for the agent that
    backs that endpoint and `main.py` for the route handler.
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
    configuration = configuration or ConfigurationReviewer(github=github)
    # `PastContextAgent` is always added to the graph — when its embedder
    # or store is None, `run()` short-circuits to an empty update. Keeps
    # the topology stable across "RAG configured" / "RAG disabled" setups.
    past_context = past_context or PastContextAgent()
    # `ReportFormatter` is always added to the graph — when
    # `settings.ENABLE_REPORT_FORMATTER=False`, its `run()` short-circuits
    # to an empty update. Same pattern as `past_context`: the topology
    # doesn't change between "formatter on" and "formatter off", so the
    # flag is a runtime toggle, not a deploy-time decision.
    report_formatter = report_formatter or ReportFormatter()

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
    graph.add_node(
        "configuration_review",
        timed_node("configuration_review")(configuration.run),
    )
    graph.add_node("review_decision", timed_node("review_decision")(review_decision.run))
    graph.add_node("aggregate_results", timed_node("aggregate_results")(coordinator.aggregate))
    graph.add_node("format_report", timed_node("format_report")(report_formatter.run))
    graph.add_node("publish_report", timed_node("publish_report")(coordinator.publish))

    graph.add_edge(START, "validate_request")
    graph.add_conditional_edges(
        "validate_request",
        route_after_validation,
        ["retrieve_past_context", "notify_rejection"],
    )
    graph.add_edge("notify_rejection", END)

    # retrieve_past_context fans out to the four security reviewers in parallel.
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

    # `format_report` sits between aggregate and publish. When
    # `ENABLE_REPORT_FORMATTER=False` (the default), the node returns
    # `{}` and the deterministic report passes through unchanged. When
    # ON, it splices a `### TL;DR` section into `state.final_report`.
    # Fail-soft on any error.
    graph.add_edge("aggregate_results", "format_report")
    graph.add_edge("format_report", "publish_report")
    graph.add_edge("publish_report", END)

    return graph.compile(checkpointer=checkpointer)
