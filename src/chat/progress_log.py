"""Periodic-log task that mirrors per-file progress to stdout/logs.

Companion to `ProgressEmitter`: the emitter publishes one envelope per
file (and surrounding `started_batch` / `finished` events) — which is
fine for the live WebSocket UI but is too noisy for the application
log. This module emits a single condensed line every
`PROGRESS_LOG_INTERVAL_SECONDS` (default 60s) summarising the state of
EVERY active reviewer for a given thread_id.

Used by `main._run_repo_review` which spawns one
`asyncio.Task(progress_log_loop(...))` per review and cancels it when
the graph completes.

Tested via `format_progress_snapshot` which is the pure-function core
of the loop — the asyncio scheduler is a thin shell over that.
"""

from __future__ import annotations

import asyncio
import logging
import time

from src.chat.progress_store import ProgressStore

logger = logging.getLogger("graph.progress")

# Roles in stable canonical order so consecutive snapshots are diffable.
_CANONICAL_ROLES = ("dependency", "injection", "owasp")


def format_progress_snapshot(
    thread_id: str,
    events: list[dict],
    elapsed_s: int,
) -> str:
    """Render the snapshot line. See module docstring for the format.

    Pure function — takes the current `ProgressStore.get(thread_id)`
    list and returns a string. No I/O, no side effects.
    """
    # First filter — we only care about per-file envelopes here. The
    # store also holds node-level `{type: progress, node: ...}` events
    # from the graph; those have their own logger.
    file_events = [e for e in events if e.get("type") == "file_progress"]

    # Aggregate per role. `seen[role]` tracks the latest snapshot we've
    # built up over the events stream.
    per_role: dict[str, dict] = {}
    for ev in file_events:
        role = ev.get("role")
        if not role:
            continue
        slot = per_role.setdefault(role, {
            "total": 0, "done": 0, "current": None, "finished": False,
        })
        state = ev.get("state")
        if state == "started_batch":
            slot["total"] = int(ev.get("total", 0))
        elif state == "file_done":
            slot["done"] += 1
            slot["current"] = ev.get("path")
        elif state == "finished":
            slot["finished"] = True
            # Trust the terminal event's totals over our running count.
            slot["done"] = int(ev.get("processed", slot["done"])) + int(
                ev.get("failed", 0)
            )
            slot["total"] = int(ev.get("total", slot["total"]))
            slot["current"] = None

    # Render in canonical order so consecutive snapshots line up
    # vertically when grep'd with `| grep progress_snapshot | sort`.
    role_tokens: list[str] = []
    for role in _CANONICAL_ROLES:
        if role not in per_role:
            continue
        slot = per_role[role]
        token = f"{role}={slot['done']}/{slot['total']}"
        if not slot["finished"] and slot["current"]:
            token += f"({slot['current']})"
        role_tokens.append(token)

    parts = [f"progress_snapshot thread_id={thread_id} elapsed_s={elapsed_s}"]
    parts.extend(role_tokens)
    return " ".join(parts)


async def progress_log_loop(
    thread_id: str,
    store: ProgressStore,
    *,
    interval_seconds: float = 60.0,
) -> None:
    """Background coroutine — emits one `format_progress_snapshot` line
    every `interval_seconds` until cancelled.

    Spawned by `_run_repo_review` right after the snapshot is set up,
    cancelled in the `finally:` block once the graph completes. Safe to
    cancel mid-sleep — `asyncio.sleep` propagates `CancelledError`
    cleanly.
    """
    start = time.monotonic()
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            elapsed = int(time.monotonic() - start)
            events = store.get(thread_id)
            line = format_progress_snapshot(thread_id, events, elapsed)
            logger.info(line)
    except asyncio.CancelledError:
        # Final snapshot on shutdown so the log carries the terminal
        # state even if the cancel landed between scheduled ticks.
        elapsed = int(time.monotonic() - start)
        events = store.get(thread_id)
        line = format_progress_snapshot(thread_id, events, elapsed)
        logger.info("%s (final)", line)
        raise
