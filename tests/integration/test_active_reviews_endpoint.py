"""`GET /reviews/active` — list in-flight reviews for the UI poll.

Contract:
  * Empty list when no reviews are running.
  * Returns one entry per `ActiveReviewsRegistry.register(...)` call
    that has not yet been `unregister(...)`-ed.
  * Each entry is the `ReviewSummary.to_dict()` shape (see
    `tests/unit/test_active_reviews.py`).
  * The list is ordered by `started_at` ascending (oldest first).

Mocks the lifespan so we can inject a registry pre-populated with
two known threads — no real graph invocation, no LLM, no DB.
"""

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from main import create_test_app
from src.chat.active_reviews import ActiveReviewsRegistry


def _stub_graph_app():
    """create_test_app accepts a graph mock — make it inert."""
    graph = MagicMock()
    graph.astream = AsyncMock()
    return graph


def test_active_endpoint_returns_empty_list_when_no_reviews():
    registry = ActiveReviewsRegistry()
    app = create_test_app(graph=_stub_graph_app(), active_reviews=registry)
    client = TestClient(app)
    r = client.get("/reviews/active")
    assert r.status_code == 200
    assert r.json() == []


def test_active_endpoint_returns_registered_entries():
    registry = ActiveReviewsRegistry()
    registry.register("tid-A", mode="repo", target="https://github.com/o/r", ref="main")
    registry.register("tid-B", mode="pr", target="https://github.com/o/r/pull/1")
    app = create_test_app(graph=_stub_graph_app(), active_reviews=registry)
    client = TestClient(app)
    r = client.get("/reviews/active")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    ids = [it["thread_id"] for it in items]
    assert ids == ["tid-A", "tid-B"]  # registration order = start time order
    # Shape sanity
    for it in items:
        assert "mode" in it and "target" in it and "ref" in it
        assert "started_at" in it and "elapsed_s" in it
        assert it["elapsed_s"] >= 0


def test_active_endpoint_drops_unregistered_entries():
    registry = ActiveReviewsRegistry()
    registry.register("tid-A", mode="repo", target="x")
    registry.register("tid-B", mode="pr", target="y")
    registry.unregister("tid-A")
    app = create_test_app(graph=_stub_graph_app(), active_reviews=registry)
    client = TestClient(app)
    r = client.get("/reviews/active")
    assert r.status_code == 200
    items = r.json()
    assert [it["thread_id"] for it in items] == ["tid-B"]
