"""A.2 — graph topology after inserting the validator and notify_rejection nodes.

These tests inspect the compiled CompiledStateGraph object directly. We don't
need to invoke the graph — just verify the nodes and the conditional edges
exist with the right successors.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.graph.coordinator import build_review_graph


@pytest.fixture(autouse=True)
def _stub_model_factory():
    """Building the graph instantiates each reviewer (and now the validator),
    each of which calls ModelFactory.get. Patch it to keep unit tests hermetic."""
    with patch("src.models.factory.ModelFactory.get", return_value=AsyncMock()):
        yield


def test_graph_has_validator_and_rejection_nodes():
    graph = build_review_graph()
    nodes = set(graph.nodes)
    assert "validate_request" in nodes
    assert "notify_rejection" in nodes


def test_start_edge_goes_only_to_validator():
    """The old `START → {dependency,injection,owasp}_review` direct edges are
    replaced by a single `START → validate_request` edge. The reviewers must
    no longer be direct successors of START."""
    graph = build_review_graph()
    start_successors = {edge.target for edge in graph.get_graph().edges if edge.source == "__start__"}
    assert start_successors == {"validate_request"}, (
        f"START should only route to validate_request; got {start_successors}"
    )


def test_validator_has_conditional_route_to_reviewers_and_rejection():
    """`validate_request` must reach both the security reviewers (accept path)
    and notify_rejection (reject path) via a conditional edge."""
    graph = build_review_graph()
    validator_successors = {
        edge.target for edge in graph.get_graph().edges if edge.source == "validate_request"
    }
    expected = {"dependency_review", "injection_review", "owasp_review", "notify_rejection"}
    assert expected.issubset(validator_successors), (
        f"validate_request should reach {expected}; got {validator_successors}"
    )
