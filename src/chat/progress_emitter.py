"""Per-thread progress envelope publisher.

Sits between the agents (which want to emit `file_progress` envelopes
during repo-mode reviews) and the I/O layer (`ProgressStore` for
replay + `ChatHub` for live WebSocket broadcast).

Reached from inside agents via a `contextvar` so we don't have to
thread explicit emitter args through every reviewer constructor. The
context var is set in `main._run_review` / `_run_repo_review` right
before the graph is invoked and reset when the graph completes; any
agent running inside that scope can call `await maybe_emit(event)`.

Concurrency contract: `contextvars` are per-task — `asyncio.gather`
copies the context to each child task, so concurrent per-file calls
inside `LLMPerFileReviewer` (PR #11 parallelism) all see the same
emitter naturally. Two concurrent reviews on different threads each
get their own emitter (covered by
`tests/unit/test_progress_emitter.py::test_use_emitter_isolates_concurrent_reviews`).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from src.chat.hub import ChatHub
from src.chat.progress_store import ProgressStore

_current: ContextVar[ProgressEmitter | None] = ContextVar(
    "progress_emitter_current", default=None
)


class ProgressEmitter:
    """Bound (`thread_id`, store, hub) handle that publishes one event
    per `emit(event)` call.

    The event dict should carry `type` and whatever role-specific
    fields the renderer needs (`role`, `state`, `path`, `index`,
    `total`, `findings_count`). We don't validate the shape here —
    `ProgressStore` is dict-typed and the chat UI is dynamic.
    """

    def __init__(self, thread_id: str, store: ProgressStore, hub: ChatHub):
        self.thread_id = thread_id
        self._store = store
        self._hub = hub

    async def emit(self, event: dict[str, Any]) -> None:
        self._store.append(self.thread_id, event)
        await self._hub.broadcast(self.thread_id, event)


def current_emitter() -> ProgressEmitter | None:
    """Active emitter for this asyncio task, or None when no review is
    in flight. Code outside a review (unit tests, CLI tools) sees None
    and should treat that as "don't emit anything"."""
    return _current.get()


@contextmanager
def use_emitter(emitter: ProgressEmitter):
    """Bind `emitter` as the current emitter for the duration of the
    `with` block. Resets the contextvar on exit, including the
    exception path."""
    token = _current.set(emitter)
    try:
        yield
    finally:
        _current.reset(token)


async def maybe_emit(event: dict[str, Any]) -> None:
    """Reviewer-side helper. Sends `event` through the active emitter
    if one is bound, silently no-ops otherwise. Safe to call from any
    asyncio task — concurrent calls inside `asyncio.gather` all see
    the same emitter via contextvar copy.
    """
    emitter = _current.get()
    if emitter is None:
        return
    await emitter.emit(event)
