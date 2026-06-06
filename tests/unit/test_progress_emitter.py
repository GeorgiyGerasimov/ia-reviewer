"""`ProgressEmitter` — per-thread progress envelope publisher.

Lives between the agents (which want to fire `{type: "file_progress",
role, state, path, index, total}` events) and the I/O layer
(`ProgressStore` for replay, `ChatHub` for live broadcast). Reached
from inside agents via a `contextvar` so we don't have to thread
explicit emitter args through every constructor.

Contract:
  * `emit(event)` appends to ProgressStore + broadcasts to ChatHub
  * `current_emitter()` returns the active emitter from contextvar, or
    None when no review is in flight (back-compat: agents that opt-in
    via `await maybe_emit(event)` are no-ops outside a review)
  * `use_emitter(emitter)` is a context manager that sets the var and
    resets it on exit (safe for nested / concurrent reviews)
"""

import pytest

from src.chat.hub import ChatHub
from src.chat.progress_emitter import (
    ProgressEmitter,
    current_emitter,
    maybe_emit,
    use_emitter,
)
from src.chat.progress_store import ProgressStore


@pytest.fixture
def store():
    return ProgressStore()


@pytest.fixture
def hub():
    return ChatHub()


# ── emit() shape ───────────────────────────────────────────────────────────


async def test_emit_writes_event_to_progress_store(store, hub):
    emitter = ProgressEmitter(thread_id="tid-1", store=store, hub=hub)
    await emitter.emit({"type": "file_progress", "role": "injection",
                        "state": "file_done", "path": "a.py",
                        "index": 1, "total": 5})

    stored = store.get("tid-1")
    assert len(stored) == 1
    assert stored[0]["type"] == "file_progress"
    assert stored[0]["role"] == "injection"
    assert stored[0]["index"] == 1


async def test_emit_broadcasts_event_to_chat_hub(store, hub, mocker):
    emitter = ProgressEmitter(thread_id="tid-2", store=store, hub=hub)
    spy = mocker.spy(hub, "broadcast")

    payload = {"type": "file_progress", "role": "owasp", "state": "started"}
    await emitter.emit(payload)

    spy.assert_called_once_with("tid-2", payload)


# ── contextvar plumbing ────────────────────────────────────────────────────


async def test_current_emitter_returns_none_outside_use_emitter(store, hub):
    """Outside an active review, agents calling current_emitter() get
    None so they can safely no-op."""
    assert current_emitter() is None


async def test_use_emitter_sets_then_resets_contextvar(store, hub):
    emitter = ProgressEmitter(thread_id="tid-3", store=store, hub=hub)
    assert current_emitter() is None
    with use_emitter(emitter):
        assert current_emitter() is emitter
    assert current_emitter() is None


async def test_use_emitter_isolates_concurrent_reviews(store, hub):
    """Two concurrent reviews must not see each other's emitter — that
    would route events to the wrong WebSocket. contextvars naturally
    handle this via per-task copies, but verify we don't accidentally
    use a shared global."""
    import asyncio

    em_a = ProgressEmitter(thread_id="tid-A", store=store, hub=hub)
    em_b = ProgressEmitter(thread_id="tid-B", store=store, hub=hub)

    async def task_a():
        with use_emitter(em_a):
            await asyncio.sleep(0.01)
            return current_emitter().thread_id

    async def task_b():
        with use_emitter(em_b):
            await asyncio.sleep(0.01)
            return current_emitter().thread_id

    a, b = await asyncio.gather(task_a(), task_b())
    assert a == "tid-A"
    assert b == "tid-B"


# ── maybe_emit helper ──────────────────────────────────────────────────────


async def test_maybe_emit_silently_skips_when_no_emitter(store, hub):
    """maybe_emit is what reviewer-side code calls. It must be safe to
    call from non-review contexts (unit tests, direct usage) — silently
    no-op rather than crash."""
    assert current_emitter() is None
    await maybe_emit({"type": "anything"})  # must not raise
    assert store.get("doesn't matter") == []


async def test_maybe_emit_routes_through_active_emitter(store, hub):
    emitter = ProgressEmitter(thread_id="tid-X", store=store, hub=hub)
    with use_emitter(emitter):
        await maybe_emit({"type": "file_progress", "path": "x.py"})

    assert len(store.get("tid-X")) == 1
    assert store.get("tid-X")[0]["path"] == "x.py"


pytestmark = pytest.mark.anyio
