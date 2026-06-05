"""In-memory chat history keyed by thread_id.

For the initial UI iteration the chat lives entirely in process memory —
restarts wipe history. Once agents start asking the human questions via
LangGraph's `interrupt()`, the chat will move into the graph's `messages`
channel (already on the Postgres checkpointer).

Per-thread message volume is bounded by review duration. The dimension
that DID leak was "many threads over time": every thread_id ever seen
lived in the dict forever. `max_threads` is a FIFO cap on the number of
threads retained; once full, the oldest-INSERTED thread is evicted on
the next `append` to a new thread.
"""

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ChatMessage:
    role: str  # "user" | "agent" | "system"
    text: str
    timestamp: str = field(default_factory=_now_iso)
    # Optional structured payload — present when the message originates
    # from a LangGraph `interrupt()` that the UI should render with
    # custom controls (currently: exploit-approval buttons). Omitted from
    # the wire payload when None so old clients still see a plain
    # role/text/timestamp tuple.
    interrupt: dict | None = None

    def to_dict(self) -> dict:
        body: dict = {"role": self.role, "text": self.text, "timestamp": self.timestamp}
        if self.interrupt is not None:
            body["interrupt"] = self.interrupt
        return body


class ChatStore:
    """In-memory per-thread chat log with an optional FIFO thread cap.

    `max_threads`:
      - `None` → unbounded (legacy default; matches existing callers).
      - `0`    → unbounded (explicit "no cap" sentinel from settings).
      - `> 0`  → keep only the N most-recently-inserted threads; evict
                 older ones when the (N+1)th NEW thread is appended.

    Note FIFO ≠ LRU: chattiness on an existing thread does NOT refresh
    its position. A long-running review can still be evicted by newer
    threads. The simpler insertion-order semantics are easier to reason
    about and sufficient for the leak we're plugging.
    """

    def __init__(self, max_threads: int | None = None) -> None:
        self._history: OrderedDict[str, list[ChatMessage]] = OrderedDict()
        self._max_threads = max_threads

    def append(self, thread_id: str, message: ChatMessage) -> None:
        is_new_thread = thread_id not in self._history
        if is_new_thread:
            self._history[thread_id] = []
            self._evict_if_needed()
        self._history[thread_id].append(message)

    def get_history(self, thread_id: str) -> list[ChatMessage]:
        return list(self._history.get(thread_id, []))

    def _evict_if_needed(self) -> None:
        if not self._max_threads or self._max_threads <= 0:
            return  # unbounded
        while len(self._history) > self._max_threads:
            self._history.popitem(last=False)  # FIFO: oldest first
