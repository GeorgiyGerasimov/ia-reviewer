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

import time
from dataclasses import dataclass


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
        register was skipped due to an exception earlier in the flow."""
        self._entries.pop(thread_id, None)

    def list_active(self) -> list[ReviewSummary]:
        """Snapshot ordered by start time (oldest first). The list is a
        shallow copy, so callers can iterate without worrying about
        registers/unregisters during the iteration."""
        return sorted(self._entries.values(), key=lambda s: s.started_at)
