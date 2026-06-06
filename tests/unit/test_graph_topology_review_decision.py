"""B.3 — review_decision is inserted between the reviewer fan-in and aggregate.

Old (Phase A): reviewers → aggregate_results
New (Phase B): reviewers → review_decision → (rerun fan-out | aggregate_results)
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.graph.coordinator import build_review_graph


@pytest.fixture(autouse=True)
def _stub_model_factory():
    with patch("src.models.factory.ModelFactory.get", return_value=AsyncMock()):
        yield


def test_graph_has_review_decision_node():
    graph = build_review_graph()
    assert "review_decision" in set(graph.nodes)


def test_reviewers_route_into_review_decision_not_aggregate_directly():
    """The Phase A edges `*_review → aggregate_results` are replaced by
    `*_review → review_decision`. The new node owns the fan-in join."""
    graph = build_review_graph()
    edges = graph.get_graph().edges
    for reviewer in (
        "dependency_review",
        "injection_review",
        "owasp_review",
        "configuration_review",
    ):
        targets = {e.target for e in edges if e.source == reviewer}
        assert targets == {"review_decision"}, (
            f"{reviewer} should route to review_decision; got {targets}"
        )


def test_review_decision_routes_to_reviewers_or_aggregate():
    """review_decision has a conditional edge that can fan back out to the
    three reviewers (rerun) or proceed to aggregate_results."""
    graph = build_review_graph()
    edges = graph.get_graph().edges
    targets = {e.target for e in edges if e.source == "review_decision"}
    expected = {
        "dependency_review",
        "injection_review",
        "owasp_review",
        "configuration_review",
        "aggregate_results",
    }
    assert expected.issubset(targets), (
        f"review_decision should reach {expected}; got {targets}"
    )
