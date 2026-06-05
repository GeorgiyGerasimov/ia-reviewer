from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.graph.coordinator import build_review_graph


@pytest.fixture(autouse=True)
def _stub_model_factory():
    """Building the graph instantiates each reviewer, which calls ModelFactory.get.
    Patch it to keep unit tests from touching real LLM clients."""
    with patch("src.models.factory.ModelFactory.get", return_value=AsyncMock()):
        yield


def test_graph_has_all_security_nodes():
    graph = build_review_graph()
    expected = {"dependency_review", "injection_review", "owasp_review", "aggregate_results", "publish_report"}
    assert expected.issubset(set(graph.nodes))


def test_graph_compiles_without_checkpointer():
    graph = build_review_graph()
    assert graph.checkpointer is None


def test_graph_accepts_memory_checkpointer():
    saver = MemorySaver()
    graph = build_review_graph(checkpointer=saver)
    assert graph.checkpointer is saver


def test_graph_accepts_injected_agents(mocker):
    coordinator = mocker.MagicMock()
    dep = mocker.MagicMock()
    inj = mocker.MagicMock()
    owasp = mocker.MagicMock()
    graph = build_review_graph(
        coordinator=coordinator, dependency=dep, injection=inj, owasp=owasp
    )
    # Smoke: graph compiles. Behaviour with these mocks is exercised in e2e tests.
    assert graph is not None
