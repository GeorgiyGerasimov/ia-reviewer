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


def test_active_endpoint_includes_live_token_counts():
    """The UI's Active reviews panel shows in-flight token usage under
    the elapsed-time line. We expose the per-thread TokenUsageHandler's
    totals (input/output/calls across ALL nodes that fired so far) so
    the UI gets a single zero-cost number per poll — no DB query, no
    extra fetch."""
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    from src.utils.token_tracking import TokenUsageHandler, _current_node, set_current_node

    handler = TokenUsageHandler()
    # Stage a few LLM calls under different nodes — totals roll up.
    for node, (in_t, out_t) in [
        ("validate_request", (100, 20)),
        ("injection_review", (5_000, 250)),
    ]:
        token = set_current_node(node)
        try:
            msg = AIMessage(
                content="ok",
                usage_metadata={
                    "input_tokens": in_t,
                    "output_tokens": out_t,
                    "total_tokens": in_t + out_t,
                },
                response_metadata={"model_name": "claude-sonnet-4-6"},
            )
            handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]))
        finally:
            _current_node.reset(token)

    registry = ActiveReviewsRegistry()
    registry.register("tid-live", mode="repo", target="https://github.com/o/r")

    app = create_test_app(graph=_stub_graph_app(), active_reviews=registry)
    # Inject the handler against the live thread_id.
    app.state.token_handlers = {"tid-live": handler}

    client = TestClient(app)
    r = client.get("/reviews/active")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    tokens = items[0]["tokens"]
    assert tokens["input"] == 5_100
    assert tokens["output"] == 270
    assert tokens["calls"] == 2


def test_active_endpoint_zero_tokens_when_handler_absent():
    """Brand-new review where no LLM call has fired yet — the tokens
    field is still present with zero values so the UI can render a
    placeholder line consistently."""
    registry = ActiveReviewsRegistry()
    registry.register("tid-cold", mode="repo", target="x")
    app = create_test_app(graph=_stub_graph_app(), active_reviews=registry)
    # No handlers registered.
    client = TestClient(app)
    r = client.get("/reviews/active")
    items = r.json()
    assert items[0]["tokens"] == {"input": 0, "output": 0, "calls": 0}


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


# ── POST /reviews/{thread_id}/cancel — Stop button backing endpoint ────────


def test_cancel_endpoint_404_for_unknown_thread():
    """Unknown thread_id → 404, body has an `error` key. Symmetrical
    with /reviews/{id} for an unregistered review."""
    registry = ActiveReviewsRegistry()
    app = create_test_app(graph=_stub_graph_app(), active_reviews=registry)
    client = TestClient(app)
    r = client.post("/reviews/does-not-exist/cancel")
    assert r.status_code == 404


def test_cancel_endpoint_404_when_task_not_attached():
    """Race: review is registered but the task handle hasn't been
    attached yet. Cancellation has nothing to act on — 404. (Practically
    a sub-millisecond window between register() and attach_task() in
    the spawn path; tested for completeness.)"""
    registry = ActiveReviewsRegistry()
    registry.register("tid-no-task", mode="repo", target="https://github.com/o/r")
    app = create_test_app(graph=_stub_graph_app(), active_reviews=registry)
    client = TestClient(app)
    r = client.post("/reviews/tid-no-task/cancel")
    assert r.status_code == 404


def test_cancel_endpoint_returns_200_when_registry_cancels():
    """Happy path: `registry.cancel(...)` reports True (a task was
    attached and was running) → endpoint returns 200 + structured body.

    We stub the registry's `cancel()` method directly: the real
    asyncio-task cancellation contract is covered by
    `tests/unit/test_active_reviews_cancel.py`. Here we only verify
    the HTTP-layer plumbing: 200 status, correct body shape, exactly
    one `cancel(thread_id)` call delivered."""
    registry = ActiveReviewsRegistry()
    registry.register("tid-live", mode="repo", target="https://github.com/o/r")
    # Pretend a task is attached and the cancel went through.
    registry.cancel = MagicMock(return_value=True)
    app = create_test_app(graph=_stub_graph_app(), active_reviews=registry)
    client = TestClient(app)
    r = client.post("/reviews/tid-live/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "cancelled", "thread_id": "tid-live"}
    registry.cancel.assert_called_once_with("tid-live")
