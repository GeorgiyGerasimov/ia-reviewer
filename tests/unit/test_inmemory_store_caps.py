"""`ChatStore` and `ProgressStore` must bound their per-thread memory.

Without an upper bound, every thread_id that's ever connected lives in
the dict forever — slow leak in any long-running deployment. PR2.4
adds a FIFO cap on the NUMBER OF THREADS each store retains; once the
cap is reached, the oldest thread's data is evicted.

Per-thread message volume is already bounded by review duration (a
review with thousands of progress events is its own problem); the
unbounded growth dimension is "many threads over time".

Cap source: `settings.IN_MEMORY_STORE_MAX_THREADS` (default 256).
Setting it to 0 disables the cap (escape hatch).
"""


from src.chat.progress_store import ProgressStore
from src.chat.store import ChatMessage, ChatStore


def _msg(text: str) -> ChatMessage:
    return ChatMessage(role="user", text=text)


# ── ChatStore ──────────────────────────────────────────────────────────


def test_chat_store_default_unbounded_for_existing_callers():
    """Without an explicit `max_threads`, the store keeps everything —
    matching today's behaviour so the rest of the test suite + small
    deployments don't change shape."""
    store = ChatStore()
    for i in range(500):
        store.append(f"tid-{i}", _msg("hi"))
    # All 500 threads retained
    for i in range(500):
        assert len(store.get_history(f"tid-{i}")) == 1


def test_chat_store_max_threads_evicts_oldest():
    """With `max_threads=3`, only the 3 most recently-WRITTEN threads
    survive. Older ones are evicted FIFO."""
    store = ChatStore(max_threads=3)
    store.append("a", _msg("1"))
    store.append("b", _msg("1"))
    store.append("c", _msg("1"))
    store.append("d", _msg("1"))  # evicts "a"

    assert store.get_history("a") == [], "oldest thread should have been evicted"
    for tid in ("b", "c", "d"):
        assert len(store.get_history(tid)) == 1, f"{tid} should still be retained"


def test_chat_store_existing_thread_does_not_count_as_new():
    """Appending to an existing thread doesn't refresh its position —
    it's still the same thread, not a new one. Tests our LRU-vs-FIFO
    semantics: a chatty thread that's been around a long time can still
    get evicted by newer ones. (This is the simpler implementation; we
    can upgrade to LRU later if needed.)"""
    store = ChatStore(max_threads=2)
    store.append("a", _msg("1"))
    store.append("b", _msg("1"))
    store.append("a", _msg("2"))  # still 2 threads
    store.append("c", _msg("1"))  # FIFO eviction: removes oldest INSERT order = "a"

    assert store.get_history("a") == []
    assert len(store.get_history("b")) == 1
    assert len(store.get_history("c")) == 1


def test_chat_store_max_threads_zero_disables_cap():
    """`max_threads=0` (the explicit "no cap" sentinel) keeps everything."""
    store = ChatStore(max_threads=0)
    for i in range(100):
        store.append(f"tid-{i}", _msg("hi"))
    # All 100 threads survive
    assert len(store.get_history("tid-0")) == 1
    assert len(store.get_history("tid-99")) == 1


# ── ProgressStore ──────────────────────────────────────────────────────


def test_progress_store_default_unbounded():
    store = ProgressStore()
    for i in range(300):
        store.append(f"t-{i}", {"node": "x"})
    assert len(store.get("t-0")) == 1
    assert len(store.get("t-299")) == 1


def test_progress_store_max_threads_evicts_oldest():
    store = ProgressStore(max_threads=2)
    store.append("a", {"node": "x"})
    store.append("b", {"node": "x"})
    store.append("c", {"node": "x"})

    assert store.get("a") == []
    assert len(store.get("b")) == 1
    assert len(store.get("c")) == 1


def test_progress_store_clear_does_not_undermine_cap_accounting():
    """`clear(thread_id)` removes a thread explicitly; it must not
    leave a phantom entry in the FIFO order that would cause the wrong
    thread to be evicted on the next append."""
    store = ProgressStore(max_threads=2)
    store.append("a", {"node": "1"})
    store.append("b", {"node": "1"})
    store.clear("a")
    store.append("c", {"node": "1"})  # should NOT evict "b"
    assert len(store.get("b")) == 1, "b was the only remaining thread and got evicted"
    assert len(store.get("c")) == 1
