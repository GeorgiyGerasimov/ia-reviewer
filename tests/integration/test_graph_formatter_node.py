"""Graph topology — the `format_report` node sits between
`aggregate_results` and `publish_report`.

The node is ALWAYS present in the graph (no conditional inclusion).
When `settings.ENABLE_REPORT_FORMATTER=False` the agent's `run()`
short-circuits to `{}`, so the node is effectively a no-op without
changing the topology. This means:
  * Toggling the flag at runtime does not require a graph rebuild.
  * Tests that assert on topology don't have to manipulate config.
  * Operators can flip the flag and see the effect immediately.
"""

from src.graph.coordinator import build_review_graph


def test_format_report_node_is_present_in_compiled_graph():
    graph = build_review_graph()
    assert "format_report" in graph.nodes


def test_aggregate_results_edges_to_format_report():
    """The edge ordering matters: aggregate writes `state.final_report`,
    format mutates it, then publish writes the result to disk."""
    graph = build_review_graph()
    # langgraph exposes edges via graph.get_graph().edges; each edge has
    # `source` / `target`. We assert the direct edge exists.
    edges = graph.get_graph().edges
    pairs = {(e.source, e.target) for e in edges}
    assert ("aggregate_results", "format_report") in pairs


def test_format_report_edges_to_publish_report():
    graph = build_review_graph()
    edges = graph.get_graph().edges
    pairs = {(e.source, e.target) for e in edges}
    assert ("format_report", "publish_report") in pairs


def test_aggregate_does_not_directly_edge_publish():
    """The old direct edge aggregate→publish must be gone — otherwise
    we'd have two paths into publish_report and the formatter could be
    bypassed."""
    graph = build_review_graph()
    edges = graph.get_graph().edges
    pairs = {(e.source, e.target) for e in edges}
    assert ("aggregate_results", "publish_report") not in pairs
