"""In-memory registry of in-flight review sessions.

Sits alongside `ProgressStore`: ProgressStore holds the EVENT log per
thread; this registry holds the *summary metadata* of every review
that's currently executing (mode, target, started_at). Used by
`GET /reviews/active` to surface a switchable list in the UI.

Lifecycle (`main.py::_run_review` and `_run_repo_review`):
  - `register(thread_id, mode, target, ref)` runs before the graph is
    invoked.
  - `unregister(thread_id)` runs in the `finally:` block — including
    the exception path. After this, the thread is no longer "active"
    even if it's still being persisted to the DB.

Thread-safety: `dict[*] = *` and `dict.pop` are atomic under the GIL,
so we don't need an explicit lock. Concurrent reviews on different
thread_ids do not contend.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class ReviewSummary:
    """Snapshot view of one in-flight review.

    Fields are deliberately limited to what the UI needs to render
    a clickable list item: identifier, what's being reviewed, when
    it started. Per-role progress comes from `ProgressStore` separately.
    """

    thread_id: str
    mode: str               # "pr" | "repo"
    target: str             # PR URL or repo URL
    ref: str = ""           # repo-mode only; empty for PR-mode
    started_at: float = 0.0  # monotonic clock seconds since registry start
    # Handle to the asyncio.Task running the review. Attached via
    # `attach_task(...)` shortly after registration so a Stop button in
    # the UI can call `.cancel()` on it. Excluded from the JSON view
    # (`to_dict`) — internal lifecycle handle, not user-facing data.
    task: asyncio.Task | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        """JSON-serialisable view consumed by the UI poll. Adds
        `elapsed_s` (seconds since started_at) so the UI doesn't have
        to compute it client-side and stay clock-synced with the server."""
        return {
            "thread_id": self.thread_id,
            "mode": self.mode,
            "target": self.target,
            "ref": self.ref,
            "started_at": self.started_at,
            "elapsed_s": max(0, int(time.monotonic() - self.started_at)),
        }


class ActiveReviewsRegistry:
    """Per-app singleton (set up in lifespan) tracking in-flight reviews."""

    def __init__(self) -> None:
        self._entries: dict[str, ReviewSummary] = {}

    def register(
        self,
        thread_id: str,
        *,
        mode: str,
        target: str,
        ref: str = "",
    ) -> None:
        """Record `thread_id` as active. Call at the START of a review
        background task, before any work that might take noticeable
        time."""
        self._entries[thread_id] = ReviewSummary(
            thread_id=thread_id,
            mode=mode,
            target=target,
            ref=ref,
            started_at=time.monotonic(),
        )

    def unregister(self, thread_id: str) -> None:
        """Remove `thread_id`. Safe to call for unknown ids — used in
        the orchestrator's `finally:` block, which fires even if
        register was skipped due to an exception earlier in the flow.
        Also drops the task handle if one was attached."""
        self._entries.pop(thread_id, None)

    def attach_task(self, thread_id: str, task: asyncio.Task) -> None:
        """Bind the underlying `asyncio.Task` for an already-registered
        review. Called by the orchestrator immediately after
        `asyncio.create_task(...)` so the Stop button in the UI has
        something to cancel.

        Silent no-op for unknown ids — callers should register first,
        but a lost race shouldn't crash the spawn path."""
        entry = self._entries.get(thread_id)
        if entry is not None:
            entry.task = task

    def get_task(self, thread_id: str) -> asyncio.Task | None:
        """Return the attached task for `thread_id`, or None if the
        entry doesn't exist or no task was attached yet."""
        entry = self._entries.get(thread_id)
        return entry.task if entry is not None else None

    def cancel(self, thread_id: str) -> bool:
        """Cancel the running review for `thread_id`.

        Returns True iff a task was attached AND was still running (i.e.
        the cancellation request was actually delivered). Returns False
        for unknown ids, for entries with no task attached, and for
        tasks that have already completed (nothing to cancel).

        The cancellation is cooperative — the task gets a
        `CancelledError` at the next `await` point. Our review
        coroutines catch it in `finally:` to run cleanup (snapshot
        rm, registry unregister, chat broadcast) before exiting.
        """
        entry = self._entries.get(thread_id)
        if entry is None or entry.task is None:
            return False
        if entry.task.done():
            return False
        entry.task.cancel()
        return True

    def list_active(self) -> list[ReviewSummary]:
        """Snapshot ordered by start time (oldest first). The list is a
        shallow copy, so callers can iterate without worrying about
        registers/unregisters during the iteration."""
        return sorted(self._entries.values(), key=lambda s: s.started_at)
