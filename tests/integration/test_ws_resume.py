"""B.5 — WebSocket detects pending graph interrupts and routes user text as
`Command(resume=...)` instead of broadcasting it as a chat message.

When there's no pending interrupt, the WS continues to behave as before
(broadcast user message to all listeners).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from main import create_test_app
from src.chat.store import ChatStore


@pytest.fixture
def store():
    return ChatStore()


def _graph_with_pending_interrupt():
    """Mock graph whose aget_state returns a snapshot with a pending interrupt
    for the current thread, and whose ainvoke (called by the resume path)
    records the Command argument."""
    graph = AsyncMock()
    snapshot = MagicMock()
    snapshot.tasks = [MagicMock(interrupts=[MagicMock(value={"question": "?"})])]
    graph.aget_state = AsyncMock(return_value=snapshot)
    return graph


def _graph_without_pending_interrupt():
    graph = AsyncMock()
    snapshot = MagicMock()
    snapshot.tasks = []
    graph.aget_state = AsyncMock(return_value=snapshot)
    return graph


def test_ws_routes_user_text_as_resume_when_interrupt_pending(mocker, store):
    graph = _graph_with_pending_interrupt()
    app = create_test_app(graph=graph, github=mocker.AsyncMock(), store=store)
    client = TestClient(app)

    with client.websocket_connect("/ws/chat/tid-resume") as ws:
        ws.send_json({"text": "rerun: extra context"})
        # User message echoed back as a normal chat broadcast
        echoed = ws.receive_json()
        assert echoed["role"] == "user"
        assert echoed["text"] == "rerun: extra context"

    # The resume path should have invoked the graph with a Command(resume=...)
    # rather than treating the message as a no-op chat broadcast.
    assert graph.ainvoke.await_count >= 1
    call = graph.ainvoke.await_args
    cmd = call.args[0]
    # langgraph.types.Command — duck-type: it has a `resume` attribute set.
    assert getattr(cmd, "resume", None) == "rerun: extra context"
    config = call.kwargs.get("config") or (call.args[1] if len(call.args) > 1 else None)
    assert config["configurable"] == {"thread_id": "tid-resume"}
    # `callbacks` now always carries the per-request TokenUsageHandler
    # (langfuse_callback is unset on this test app).
    assert "callbacks" in config and len(config["callbacks"]) == 1


def test_ws_broadcasts_normally_when_no_interrupt(mocker, store):
    graph = _graph_without_pending_interrupt()
    app = create_test_app(graph=graph, github=mocker.AsyncMock(), store=store)
    client = TestClient(app)

    with client.websocket_connect("/ws/chat/tid-normal") as ws:
        ws.send_json({"text": "hello"})
        echoed = ws.receive_json()
        assert echoed["text"] == "hello"

    # No pending interrupt → no resume call
    graph.ainvoke.assert_not_called()


async def test_run_review_broadcasts_pending_interrupt_question(mocker, store):
    """After graph.ainvoke pauses at an interrupt, _run_review must surface
    the question to the chat panel (store + hub)."""
    from main import _run_review
    from src.chat.hub import ChatHub
    from src.graph.state import ReviewRequest

    fake_app = mocker.MagicMock()
    fake_app.state.github.fetch_pr = mocker.AsyncMock(
        return_value=ReviewRequest(
            pr_url="https://github.com/o/r/pull/1",
            diff="d",
            files_changed=["a.py"],
            author="dev",
        )
    )
    fake_app.state.graph.ainvoke = mocker.AsyncMock()
    snapshot = MagicMock()
    snapshot.tasks = [MagicMock(interrupts=[MagicMock(value={"question": "approve rerun?"})])]
    fake_app.state.graph.aget_state = mocker.AsyncMock(return_value=snapshot)
    fake_app.state.chat_store = store
    fake_app.state.chat_hub = ChatHub()  # real hub; nothing connected → broadcast is no-op

    await _run_review(fake_app, "https://github.com/o/r/pull/1", [], "tid-q")

    history = store.get_history("tid-q")
    assert any(m.role == "agent" and "approve rerun?" in m.text for m in history)
