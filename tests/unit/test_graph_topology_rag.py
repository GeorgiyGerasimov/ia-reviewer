"""Graph topology with the RAG node inserted between validate_request
(accept branch) and the security-reviewer fan-out.

After this change:
  START → validate_request
            ├─reject──→ notify_rejection → END
            └─accept──→ retrieve_past_context → [dependency, injection, owasp]
                                                       ↓ fan-in
                                                ... (unchanged from here on)

The retrieve_past_context node is ALWAYS in the graph — when no embedder
or store is configured the agent's own `run()` short-circuits to an empty
update. Single topology, single test surface.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.graph.coordinator import build_review_graph


@pytest.fixture(autouse=True)
def _stub_model_factory():
    with patch("src.models.factory.ModelFactory.get", return_value=AsyncMock()):
        yield


def test_graph_has_retrieve_past_context_node():
    graph = build_review_graph()
    assert "retrieve_past_context" in set(graph.nodes)


def test_validator_accept_route_goes_through_retrieve_past_context():
    """`validate_request` (accept) → retrieve_past_context, NOT directly
    to the reviewers. Reject branch still goes straight to notify_rejection."""
    graph = build_review_graph()
    validator_successors = {
        edge.target for edge in graph.get_graph().edges if edge.source == "validate_request"
    }
    # Accept side must include retrieve_past_context.
    assert "retrieve_past_context" in validator_successors
    # Reject side still routes to notify_rejection.
    assert "notify_rejection" in validator_successors
    # Reviewers are NO LONGER direct successors of validate_request — they're
    # one hop further now, behind retrieve_past_context.
    assert "dependency_review" not in validator_successors
    assert "injection_review" not in validator_successors
    assert "owasp_review" not in validator_successors


def test_retrieve_past_context_fans_out_to_all_reviewers():
    """The retrieve_past_context node MUST hand off to all three security
    reviewers in parallel — single source, three targets."""
    graph = build_review_graph()
    rag_successors = {
        edge.target for edge in graph.get_graph().edges
        if edge.source == "retrieve_past_context"
    }
    expected = {
        "dependency_review",
        "injection_review",
        "owasp_review",
        "configuration_review",
    }
    assert expected.issubset(rag_successors), (
        f"retrieve_past_context should fan out to {expected}; got {rag_successors}"
    )
