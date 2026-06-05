"""Node timing decorator — wraps a graph node, logs wall-clock duration.

Contract:
- `timed_node(name)(fn)` returns an async wrapper that:
  - calls the underlying `fn(state)` and returns its result unchanged
  - logs ONE structured line on success:
      `node_complete node=<name> thread_id=<id> duration_ms=<int> status=ok`
  - on exception, logs the same line with `status=error error=<repr>`
    and re-raises
- `thread_id` comes from `state.thread_id` if present, else `-`.
- `duration_ms` is the wall-clock measurement (perf_counter), integer.

These tests verify the structured log format precisely so downstream
parsers (`grep node_complete | awk` or jq) keep working when the
helper changes.
"""

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from src.graph.state import ReviewState
from src.utils.node_timing import timed_node

# ── happy path ────────────────────────────────────────────────────────────────


async def test_timed_node_returns_underlying_result_unchanged():
    async def inner(state):
        return {"answer": 42}

    wrapped = timed_node("my_node")(inner)
    out = await wrapped(ReviewState(thread_id="abc"))

    assert out == {"answer": 42}


async def test_timed_node_logs_node_complete_on_success(caplog):
    async def inner(state):
        return {}

    wrapped = timed_node("validate_request")(inner)
    with caplog.at_level(logging.INFO, logger="graph.timing"):
        await wrapped(ReviewState(thread_id="thread-xyz"))

    timing_lines = [
        r.getMessage() for r in caplog.records
        if r.name == "graph.timing"
    ]
    assert len(timing_lines) == 1
    line = timing_lines[0]
    assert "node_complete" in line
    assert "node=validate_request" in line
    assert "thread_id=thread-xyz" in line
    assert "status=ok" in line
    assert "duration_ms=" in line


async def test_timed_node_uses_dash_when_thread_id_missing(caplog):
    async def inner(state):
        return {}

    wrapped = timed_node("publish_report")(inner)
    with caplog.at_level(logging.INFO, logger="graph.timing"):
        # No thread_id set — defaults to empty string in state
        await wrapped(ReviewState())

    line = next(r.getMessage() for r in caplog.records if r.name == "graph.timing")
    assert "thread_id=-" in line, (
        "missing thread_id must render as '-' so log parsers see a non-empty "
        "field instead of `thread_id=` with no value"
    )


async def test_timed_node_measures_duration(caplog):
    async def inner(state):
        # Sleep for ~30ms so we can assert a non-trivial lower bound.
        await asyncio.sleep(0.030)
        return {}

    wrapped = timed_node("slow_node")(inner)
    with caplog.at_level(logging.INFO, logger="graph.timing"):
        await wrapped(ReviewState(thread_id="t"))

    line = next(r.getMessage() for r in caplog.records if r.name == "graph.timing")
    # Extract duration_ms=NN — must be a positive integer >= 20 (give
    # a healthy lower bound on a slow CI runner; we slept 30ms).
    import re
    match = re.search(r"duration_ms=(\d+)", line)
    assert match, f"no duration_ms in log line: {line!r}"
    duration = int(match.group(1))
    assert duration >= 20, (
        f"duration_ms={duration} too small — wrapper must measure real wall-clock"
    )
    assert duration < 5000, (
        f"duration_ms={duration} suspiciously large for a 30ms sleep"
    )


# ── error path ────────────────────────────────────────────────────────────────


async def test_timed_node_logs_and_reraises_on_exception(caplog):
    class _BoomError(RuntimeError):
        pass

    async def inner(state):
        raise _BoomError("kaboom")

    wrapped = timed_node("dependency_review")(inner)

    with caplog.at_level(logging.INFO, logger="graph.timing"):
        with pytest.raises(_BoomError, match="kaboom"):
            await wrapped(ReviewState(thread_id="err-thread"))

    line = next(r.getMessage() for r in caplog.records if r.name == "graph.timing")
    assert "node=dependency_review" in line
    assert "thread_id=err-thread" in line
    assert "status=error" in line
    assert "duration_ms=" in line
    # The error message must be in the line — debugging-friendly.
    assert "kaboom" in line


# ── argument-shape tests ──────────────────────────────────────────────────────


async def test_timed_node_works_on_state_without_attribute(caplog):
    """`state.thread_id` is the canonical accessor; objects without that
    attribute should still work (defensive fallback to '-')."""
    async def inner(state):
        return {}

    wrapped = timed_node("aggregate_results")(inner)
    # Use a MagicMock that lacks `thread_id` configured — getattr returns
    # the MagicMock itself by default; we need to test with an object
    # that explicitly doesn't have the attr.
    state = MagicMock(spec=[])  # spec=[] means no attributes at all

    with caplog.at_level(logging.INFO, logger="graph.timing"):
        await wrapped(state)

    line = next(r.getMessage() for r in caplog.records if r.name == "graph.timing")
    assert "thread_id=-" in line
