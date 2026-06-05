"""Wall-clock timing decorator for LangGraph nodes.

Every graph node is wrapped with `timed_node("<name>")` in
`src/graph/coordinator.py::build_review_graph`. Each invocation emits
one structured log line on a dedicated logger (`graph.timing`) so the
operator can:

  * grep the app log for `node_complete` to see all node timings,
  * `awk` or `jq` on `duration_ms` to compute p50 / p95 / mean per node,
  * plot graphs from saved logs over time (regression detection),
  * correlate by `thread_id` to reconstruct a single review's timeline.

Log format (stable — downstream parsers depend on it):

    node_complete node=<name> thread_id=<id> duration_ms=<int> status=ok
    node_complete node=<name> thread_id=<id> duration_ms=<int> status=error error=<repr>

Field order is fixed. `thread_id=-` when the state lacks one (start-of-run
edge cases). Duration is `time.perf_counter()` based — monotonic, immune
to wall-clock jumps.

The wrapper:
  * returns the inner coroutine's result unchanged on success,
  * re-raises any exception after logging (does NOT swallow),
  * never mutates `state` itself (no `state.timings = ...`) so checkpoints
    stay clean.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable

from src.utils.logger import get_logger

logger = get_logger("graph.timing")


def timed_node(name: str) -> Callable[
    [Callable[..., Awaitable[dict]]],
    Callable[..., Awaitable[dict]],
]:
    """Wrap a graph node so its wall-clock duration is logged.

    Usage in `build_review_graph`:

        graph.add_node("validate_request", timed_node("validate_request")(validator.run))

    Args:
        name: Stable log label for this node. Use the same string as the
              graph node name so log lines correspond 1:1 with the
              compiled topology.

    Returns:
        A decorator. The decorated function MUST be an async function
        accepting `state` as its first positional argument and returning
        a partial-state dict.
    """

    def decorator(fn: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
        @functools.wraps(fn)
        async def wrapper(state, *args, **kwargs):
            thread_id = _extract_thread_id(state)
            t0 = time.perf_counter()
            try:
                result = await fn(state, *args, **kwargs)
            except BaseException as exc:
                duration_ms = _elapsed_ms(t0)
                # Use repr to keep the message machine-parseable
                # (no embedded newlines from arbitrary exception types).
                err_repr = repr(str(exc))
                logger.warning(
                    "node_complete node=%s thread_id=%s duration_ms=%d status=error error=%s",
                    name,
                    thread_id,
                    duration_ms,
                    err_repr,
                )
                raise
            duration_ms = _elapsed_ms(t0)
            logger.info(
                "node_complete node=%s thread_id=%s duration_ms=%d status=ok",
                name,
                thread_id,
                duration_ms,
            )
            return result

        return wrapper

    return decorator


def _extract_thread_id(state) -> str:
    """Pull `thread_id` off state; fall back to '-' on absent/empty."""
    tid = getattr(state, "thread_id", None) or ""
    if not isinstance(tid, str) or not tid:
        return "-"
    return tid


def _elapsed_ms(t0: float) -> int:
    """Wall-clock ms since `t0` (from `time.perf_counter()`)."""
    return int(round((time.perf_counter() - t0) * 1000))
