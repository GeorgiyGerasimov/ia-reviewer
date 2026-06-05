"""In-memory store of workflow-progress events keyed by thread_id.

The chat WebSocket broadcasts `{"type": "progress", "node": <name>}`
envelopes whenever a LangGraph node completes — but only currently-
connected sockets receive them. The frontend's BackgroundTask race means
the client almost always misses the first few events before the WS
handshake finishes.

This store mirrors those events so the chat WS can replay them on
connect, just like it already does for `ChatStore` history. Terminal
`__done__` event is included; the UI uses it to gray out circles whose
nodes never fired.
"""

from collections import OrderedDict


class ProgressStore:
    """Per-thread progress-event log with the same FIFO thread cap as
    `ChatStore`. See its docstring for the cap semantics — identical
    contract here.
    """

    def __init__(self, max_threads: int | None = None) -> None:
        self._events: OrderedDict[str, list[dict]] = OrderedDict()
        self._max_threads = max_threads

    def append(self, thread_id: str, event: dict) -> None:
        if thread_id not in self._events:
            self._events[thread_id] = []
            self._evict_if_needed()
        self._events[thread_id].append(event)

    def get(self, thread_id: str) -> list[dict]:
        return list(self._events.get(thread_id, []))

    def clear(self, thread_id: str) -> None:
        """Explicit removal (e.g. when a review resets). Removing here
        also frees the FIFO slot — the next NEW thread won't trigger
        an unnecessary eviction."""
        self._events.pop(thread_id, None)

    def _evict_if_needed(self) -> None:
        if not self._max_threads or self._max_threads <= 0:
            return
        while len(self._events) > self._max_threads:
            self._events.popitem(last=False)
