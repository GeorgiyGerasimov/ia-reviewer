"""`format_progress_snapshot` — pure-function summariser for the
periodic-log task.

The periodic task itself runs on `asyncio.sleep(60)` and is hard to
unit-test directly. So we extract the SHAPE of the snapshot into a
pure function that takes the current `ProgressStore` view of a thread
and returns the log line. Tests pin the shape — the scheduler is
trivial enough to read in src.

Snapshot format (one line, single space separated, stable field
order, matches our existing `graph.timing` log convention):

  progress_snapshot thread_id=<tid> elapsed_s=<int>
      injection=<done>/<total>(<current>) owasp=<done>/<total>(<current>)
      ...

Roles with no events yet are omitted. A role that's `finished` shows
`done` == `total` and no `(current)`.
"""

from src.chat.progress_log import format_progress_snapshot


def _ev(role: str, state: str, **extra) -> dict:
    return {"type": "file_progress", "role": role, "state": state, **extra}


# ── basics ─────────────────────────────────────────────────────────────────


def test_empty_events_returns_minimal_line():
    """Thread has no progress events yet → snapshot still emits the
    bare frame so operators can grep for ANY review activity by
    `thread_id`. No role columns."""
    out = format_progress_snapshot(thread_id="tid-1", events=[], elapsed_s=42)
    assert "thread_id=tid-1" in out
    assert "elapsed_s=42" in out
    # no role tokens
    assert "injection=" not in out
    assert "owasp=" not in out


def test_started_batch_only_shows_zero_of_total():
    """Single role, started_batch only, no file_done yet → 0/N."""
    events = [_ev("injection", "started_batch", total=47, paths=["a.py"])]
    out = format_progress_snapshot("tid", events, elapsed_s=5)
    assert "injection=0/47" in out


def test_partial_done_shows_running_count_and_current_file():
    """Several file_done events seen → done count + the LAST seen
    `path` rendered as the "current" file (gives operators a useful
    cursor even though completions are not strictly ordered under
    asyncio.gather)."""
    events = [
        _ev("injection", "started_batch", total=5, paths=[f"f{i}.py" for i in range(5)]),
        _ev("injection", "file_done", path="f0.py", index=1, total=5, findings_count=0),
        _ev("injection", "file_done", path="f1.py", index=2, total=5, findings_count=2),
    ]
    out = format_progress_snapshot("tid", events, elapsed_s=30)
    assert "injection=2/5" in out
    # Last seen file_done's path becomes the "current" hint
    assert "(f1.py)" in out


def test_finished_role_shows_full_count_without_current():
    """A role that emitted `finished` is done — no point showing a
    'current' cursor. Done count == total."""
    events = [
        _ev("injection", "started_batch", total=3, paths=["a.py", "b.py", "c.py"]),
        _ev("injection", "file_done", path="a.py", index=1, total=3, findings_count=0),
        _ev("injection", "file_done", path="b.py", index=2, total=3, findings_count=1),
        _ev("injection", "file_done", path="c.py", index=3, total=3, findings_count=0),
        _ev("injection", "finished", processed=3, failed=0, findings_total=1, total=3),
    ]
    out = format_progress_snapshot("tid", events, elapsed_s=60)
    assert "injection=3/3" in out
    # No "(current)" parenthetical after finished
    assert "injection=3/3 " in out or out.endswith("injection=3/3")


def test_multiple_roles_rendered_in_stable_order():
    """When several reviewers are active, roles appear in the canonical
    order (dependency, injection, owasp) so operators eyeballing the log
    can compare runs."""
    events = [
        _ev("owasp", "started_batch", total=10, paths=[]),
        _ev("owasp", "file_done", path="x.py", index=1, total=10, findings_count=0),
        _ev("injection", "started_batch", total=5, paths=[]),
        _ev("injection", "file_done", path="y.py", index=1, total=5, findings_count=0),
        _ev("dependency", "started_batch", total=2, paths=[]),
    ]
    out = format_progress_snapshot("tid", events, elapsed_s=10)
    # dependency before injection before owasp
    dep_pos = out.find("dependency=")
    inj_pos = out.find("injection=")
    owasp_pos = out.find("owasp=")
    assert dep_pos < inj_pos < owasp_pos


def test_failed_files_counted_separately_in_done():
    """`file_done` with failed=True still bumps the done count (the
    file is no longer in-progress, just unsuccessfully so). Operators
    glance at the snapshot, see N/total, and only chase the per-file
    log lines if N < total at the end."""
    events = [
        _ev("injection", "started_batch", total=3, paths=[]),
        _ev("injection", "file_done", path="a.py", index=1, total=3, findings_count=0, failed=False),
        _ev("injection", "file_done", path="b.py", index=2, total=3, findings_count=0, failed=True),
    ]
    out = format_progress_snapshot("tid", events, elapsed_s=10)
    assert "injection=2/3" in out


# ── unrelated events ignored ───────────────────────────────────────────────


def test_non_file_progress_events_are_ignored():
    """The shared progress store also receives node-level
    `{type: progress, node: ...}` envelopes from the graph. The
    snapshotter must filter those out — we only summarise per-file
    activity."""
    events = [
        {"type": "progress", "node": "validate_request", "status": "fired"},
        {"type": "progress", "node": "__done__"},
        _ev("injection", "started_batch", total=2, paths=[]),
        _ev("injection", "file_done", path="a.py", index=1, total=2, findings_count=0),
    ]
    out = format_progress_snapshot("tid", events, elapsed_s=5)
    assert "injection=1/2" in out
    assert "validate_request" not in out
