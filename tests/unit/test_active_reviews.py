"""`ActiveReviewsRegistry` — in-memory tracker of in-flight reviews.

Sits alongside ProgressStore: ProgressStore holds the EVENT log per
thread; this registry holds the *summary metadata* of every review
that's currently executing (mode, target, started_at). Used by
`GET /reviews/active` to surface a switchable list in the UI.

Lifecycle:
  * `register(thread_id, mode, target, ref)` — called when a review
    background task starts (before clone / validate / graph invoke).
  * `unregister(thread_id)` — called in the `finally:` block, including
    the exception path. After this, the thread is no longer "active"
    even if it's still being persisted to the DB.
  * `list_active()` returns a snapshot list ordered by start time
    (oldest first — newest at the bottom matches the way operators
    visually scan a queue).

Concurrent registers / unregisters are safe — the registry uses no
locks because dict ops are atomic under GIL.
"""

import time

import pytest

from src.chat.active_reviews import ActiveReviewsRegistry


@pytest.fixture
def registry():
    return ActiveReviewsRegistry()


def test_register_then_list_returns_one_entry(registry):
    registry.register("tid-1", mode="repo", target="https://github.com/o/r", ref="main")
    items = registry.list_active()
    assert len(items) == 1
    assert items[0].thread_id == "tid-1"
    assert items[0].mode == "repo"
    assert items[0].target == "https://github.com/o/r"
    assert items[0].ref == "main"
    # started_at is set automatically
    assert items[0].started_at > 0


def test_unregister_removes_entry(registry):
    registry.register("tid-1", mode="pr", target="https://github.com/o/r/pull/1")
    registry.unregister("tid-1")
    assert registry.list_active() == []


def test_unregister_unknown_thread_is_noop(registry):
    """The `finally:` block calls unregister even when register was
    never reached (e.g. the registry didn't get a chance to record the
    thread before an exception). Must not raise."""
    registry.unregister("ghost-tid")  # should not raise


def test_list_active_ordered_by_start_time(registry):
    """List in chronological order (oldest first) so operators can
    visually scan the queue top-to-bottom and see the longest-running
    review at the top."""
    registry.register("tid-A", mode="repo", target="x")
    time.sleep(0.01)
    registry.register("tid-B", mode="repo", target="y")
    time.sleep(0.01)
    registry.register("tid-C", mode="pr", target="z")
    ids = [s.thread_id for s in registry.list_active()]
    assert ids == ["tid-A", "tid-B", "tid-C"]


def test_register_overwrites_prior_entry_for_same_thread(registry):
    """If somehow register is called twice for the same thread_id (e.g.
    a retry path), the second call REPLACES the first — we don't want
    duplicate entries in the active list."""
    registry.register("tid-1", mode="pr", target="old")
    registry.register("tid-1", mode="repo", target="new")
    items = registry.list_active()
    assert len(items) == 1
    assert items[0].target == "new"
    assert items[0].mode == "repo"


def test_review_summary_to_dict_serialisable(registry):
    """The /reviews/active endpoint returns JSON — ReviewSummary must
    have a `to_dict()` (or be a dataclass that jsonable_encoder
    handles) that produces the keys the UI consumes."""
    registry.register(
        "tid-1", mode="repo", target="https://github.com/o/r", ref="main"
    )
    items = registry.list_active()
    d = items[0].to_dict()
    assert d["thread_id"] == "tid-1"
    assert d["mode"] == "repo"
    assert d["target"] == "https://github.com/o/r"
    assert d["ref"] == "main"
    assert isinstance(d["started_at"], (int, float))
    assert "elapsed_s" in d
    assert d["elapsed_s"] >= 0
