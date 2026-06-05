"""`_pending_timeouts` must not leak entries.

Today's code adds a Task to the dict in `_schedule_exploit_timeout` and
only removes it via:
  (a) `_schedule_exploit_timeout` itself, when a NEW interrupt for the
      same thread fires;
  (b) `_cancel_pending_timeout`, called from `_resume_review` when the
      human ACTUALLY answers in time.

If neither happens — the user simply never responds — the timer
completes normally, runs `_default_decline_after_timeout`, and the
entry stays. Long-running deployments accumulate dead entries.

After PR2.3 the timeout coro removes its own entry in `finally:`.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from main import (
    _pending_timeouts,
    _schedule_exploit_timeout,
)


@pytest.fixture(autouse=True)
def _clear_global():
    """Each test runs with an empty timeouts dict to keep them independent."""
    _pending_timeouts.clear()
    yield
    _pending_timeouts.clear()


@pytest.fixture
def app_with_chat(mocker):
    """An app mock just rich enough to run `_default_decline_after_timeout`:
    chat store + hub + `_resume_review` short-circuited so we don't need
    a real LangGraph."""
    app = mocker.MagicMock()
    app.state.chat_store = mocker.MagicMock()
    app.state.chat_hub = mocker.AsyncMock()
    # _resume_review is awaited at the end of the timeout coro; replace
    # with a no-op AsyncMock so the test doesn't need a real graph.
    mocker.patch("main._resume_review", new=AsyncMock())
    # Short-circuit the actual sleep so the test doesn't wait 60s.
    mocker.patch("main.EXPLOIT_TIMEOUT_SECONDS", 0.01)
    return app


async def test_pending_timeouts_entry_is_removed_after_timeout_fires(app_with_chat):
    """The unhappy path: nobody replies, timeout completes naturally,
    and the dict entry must be gone."""
    _schedule_exploit_timeout(app_with_chat, "tid-clean", "abc123")
    assert "tid-clean" in _pending_timeouts

    # Wait for the timer to finish (mocked EXPLOIT_TIMEOUT_SECONDS=0.01).
    task = _pending_timeouts["tid-clean"]
    await task
    # Give the event loop a tick for the finally: clause to run.
    await asyncio.sleep(0)

    assert "tid-clean" not in _pending_timeouts, (
        "After timeout fires, the entry must be removed so long-running "
        f"deployments don't accumulate dead tasks. Dict: {dict(_pending_timeouts)}"
    )


async def test_pending_timeouts_entry_cleaned_after_cancel(app_with_chat):
    """The happy path (user replies → `_cancel_pending_timeout` cancels
    the task) already removes the entry — but verify the contract:
    after a cancel, the dict has no entry, no zombie tasks."""
    from main import _cancel_pending_timeout

    _schedule_exploit_timeout(app_with_chat, "tid-cancel", "xyz")
    assert "tid-cancel" in _pending_timeouts
    _cancel_pending_timeout("tid-cancel")
    assert "tid-cancel" not in _pending_timeouts


async def test_pending_timeouts_two_threads_independent(app_with_chat, mocker):
    """Timeouts for different threads must clean their own entries
    without affecting each other.

    Tricky bit: we capture the task references BEFORE the first await,
    because the coro itself pops the entry in `finally:`. Looking up
    the dict between awaits would race against self-cleanup.
    """
    _schedule_exploit_timeout(app_with_chat, "tid-a", "fa")
    _schedule_exploit_timeout(app_with_chat, "tid-b", "fb")
    task_a = _pending_timeouts["tid-a"]
    task_b = _pending_timeouts["tid-b"]
    assert {"tid-a", "tid-b"}.issubset(_pending_timeouts.keys())

    await task_a
    await task_b
    await asyncio.sleep(0)
    assert _pending_timeouts == {}, (
        f"both timeouts should self-clean; got {dict(_pending_timeouts)}"
    )


async def test_default_decline_finally_clears_even_on_error(app_with_chat, mocker):
    """If `_resume_review` raises (e.g. graph crashed), the entry must
    STILL be removed — otherwise a crash leaks the dict permanently."""
    mocker.patch("main._resume_review", side_effect=RuntimeError("graph kaput"))
    _schedule_exploit_timeout(app_with_chat, "tid-err", "z")
    task = _pending_timeouts["tid-err"]
    # Errors inside the coro propagate to `await task` — that's fine,
    # but the finally: cleanup MUST run before the exception escapes.
    with pytest.raises(RuntimeError):
        await task
    await asyncio.sleep(0)
    assert "tid-err" not in _pending_timeouts


async def test_resume_text_for_decline_uses_finding_id(app_with_chat, mocker):
    """Sanity check on scheduler — the decline payload routed via
    `_resume_review` includes the finding id, regardless of the
    cleanup-in-finally change.
    """
    resume_mock = mocker.patch("main._resume_review", new=AsyncMock())
    _schedule_exploit_timeout(app_with_chat, "tid-sanity", "abcdef")
    task = _pending_timeouts["tid-sanity"]
    await task
    # `_resume_review(app, "tid-sanity", "decline abcdef")` was called.
    resume_mock.assert_awaited_once()
    call_args = resume_mock.await_args
    assert call_args.args[1] == "tid-sanity"
    assert "decline abcdef" in call_args.args[2]
