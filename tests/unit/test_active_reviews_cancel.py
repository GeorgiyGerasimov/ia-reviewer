"""ActiveReviewsRegistry — task tracking + cancellation.

Contract for the cancel feature:
  * Each active review can OPTIONALLY hold a reference to its underlying
    `asyncio.Task`, attached after `register(...)` via `attach_task(...)`.
  * `cancel(thread_id)` returns True iff a task is attached AND not yet
    done, then calls `.cancel()` on it. Returns False for unknown ids,
    for finished tasks, and for entries with no task attached.
  * `unregister(...)` clears the task reference too (no stale handles
    after the task completes its own `finally:`).
"""

from __future__ import annotations

import asyncio

import pytest

from src.chat.active_reviews import ActiveReviewsRegistry

pytestmark = pytest.mark.anyio


async def test_attach_task_stores_reference() -> None:
    reg = ActiveReviewsRegistry()
    reg.register("t-1", mode="repo", target="https://github.com/o/r", ref="main")

    async def _noop():
        await asyncio.sleep(60)
    task = asyncio.create_task(_noop())
    reg.attach_task("t-1", task)
    try:
        assert reg.get_task("t-1") is task
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_cancel_unknown_returns_false() -> None:
    reg = ActiveReviewsRegistry()
    assert reg.cancel("never-existed") is False


async def test_cancel_without_attached_task_returns_false() -> None:
    """register() alone is NOT enough — there must be a task handle."""
    reg = ActiveReviewsRegistry()
    reg.register("t-2", mode="pr", target="https://github.com/o/r/pull/1")
    assert reg.cancel("t-2") is False


async def test_cancel_known_task_cancels_and_returns_true() -> None:
    reg = ActiveReviewsRegistry()
    reg.register("t-3", mode="repo", target="https://github.com/o/r")

    started = asyncio.Event()
    done = asyncio.Event()

    async def _busy():
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            done.set()

    task = asyncio.create_task(_busy())
    reg.attach_task("t-3", task)
    await started.wait()

    assert reg.cancel("t-3") is True
    # Cooperative cancellation — give the task a chance to observe it.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert done.is_set()


async def test_cancel_already_finished_task_returns_false() -> None:
    """A task that has already completed cannot be cancelled."""
    reg = ActiveReviewsRegistry()
    reg.register("t-4", mode="repo", target="https://github.com/o/r")

    async def _quick():
        return "done"

    task = asyncio.create_task(_quick())
    reg.attach_task("t-4", task)
    await task  # let it finish naturally
    assert reg.cancel("t-4") is False


async def test_unregister_clears_task_handle() -> None:
    """After unregister, get_task returns None even if the task object
    is still reachable elsewhere — no stale references in the registry."""
    reg = ActiveReviewsRegistry()
    reg.register("t-5", mode="repo", target="https://github.com/o/r")

    async def _noop():
        await asyncio.sleep(60)
    task = asyncio.create_task(_noop())
    reg.attach_task("t-5", task)
    try:
        reg.unregister("t-5")
        assert reg.get_task("t-5") is None
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
