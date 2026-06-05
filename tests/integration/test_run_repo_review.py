"""Integration tests for `_run_repo_review` orchestration in main.py.

Phase 5 ties together fetch_tree (Phase 2), per-file reviewers (Phase 3),
and report publishing (Phase 4):

  1. Fetch the repo tree, populate `state.request.repo_files`.
  2. Stamp `state.thread_id` so CoordinatorAgent can write
     `reports/<thread_id>.md` in publish.
  3. After the graph completes, broadcast a brief summary into the chat
     with a link to `/reports/<thread_id>.md`.
"""

import pytest

from main import _run_repo_review
from src.chat.hub import ChatHub
from src.chat.store import ChatStore
from src.graph.state import RepoFile


@pytest.fixture
def fake_app(mocker, tmp_path):
    app = mocker.MagicMock()
    app.state.langfuse_callback = None
    # Repo mode now uses local `git clone` instead of GitHub REST. Patch the
    # cloning helper to return a known snapshot dir and a fixed file list so
    # the orchestration test stays hermetic.
    snapshot = tmp_path / "snap"
    snapshot.mkdir()
    mocker.patch(
        "main.clone_repo",
        return_value=snapshot,
    )
    mocker.patch(
        "main.list_repo_files",
        return_value=[
            RepoFile(path="src/app.py", content="", size=100),
            RepoFile(path="README.md", content="", size=50),
        ],
    )
    app.state.snapshot = snapshot
    # Default astream yields a single "publish_report" chunk so tests that
    # don't override it still see graph completion.
    _stream_invocations: list[tuple] = []

    async def default_astream(state, config=None):
        _stream_invocations.append((state, config))
        yield {"publish_report": {}}

    app.state.graph.astream = default_astream
    app.state._stream_invocations = _stream_invocations
    # Snapshot returned by aget_state — no pending interrupts in this scenario.
    snapshot = mocker.MagicMock()
    snapshot.tasks = []
    app.state.graph.aget_state = mocker.AsyncMock(return_value=snapshot)
    app.state.chat_store = ChatStore()
    app.state.chat_hub = ChatHub()
    app.state.reports_dir = tmp_path
    return app


async def test_run_repo_review_clones_and_populates_state(fake_app):
    await _run_repo_review(
        fake_app,
        repo_url="https://github.com/o/r",
        ref="main",
        scope=["injection"],
        thread_id="tid-x",
    )

    assert fake_app.state._stream_invocations, "expected graph.astream to be called"

    state, _config = fake_app.state._stream_invocations[0]
    assert state.request.mode == "repo"
    assert state.request.repo_url == "https://github.com/o/r"
    assert state.request.ref == "main"
    assert state.request.scope == ["injection"]
    # repo_files populated from list_repo_files(snapshot)
    paths = [f.path for f in state.request.repo_files]
    assert paths == ["src/app.py", "README.md"]
    assert state.request.snapshot_dir == str(fake_app.state.snapshot)
    # thread_id stamped so CoordinatorAgent can write reports/tid-x.md
    assert state.thread_id == "tid-x"


async def test_run_repo_review_emits_clone_progress_active_then_fired(fake_app, mocker):
    """The clone step is a long-running pre-graph phase; the UI shows a
    spinning indicator while it runs. To drive that, _run_repo_review must
    broadcast `clone_repo` with status `active` BEFORE the clone subprocess
    and `fired` AFTER it succeeds — in that order."""
    broadcast_mock = mocker.spy(fake_app.state.chat_hub, "broadcast")

    await _run_repo_review(
        fake_app,
        repo_url="https://github.com/o/r",
        ref="main",
        scope=[],
        thread_id="tid-clone",
    )

    progress = [
        c.args[1]
        for c in broadcast_mock.await_args_list
        if isinstance(c.args[1], dict)
        and c.args[1].get("type") == "progress"
        and c.args[1].get("node") == "clone_repo"
    ]
    statuses = [e.get("status") for e in progress]
    assert statuses == ["active", "fired"], (
        f"expected clone_repo: active → fired, got {statuses!r}"
    )


async def test_run_repo_review_cleans_up_snapshot_in_finally(fake_app):
    """The cloned snapshot directory must be removed once the graph finishes
    (or errors out). Otherwise repeated repo reviews leak /tmp directories."""
    snapshot = fake_app.state.snapshot
    assert snapshot.exists()

    await _run_repo_review(
        fake_app,
        repo_url="https://github.com/o/r",
        ref="main",
        scope=[],
        thread_id="tid-cleanup",
    )

    assert not snapshot.exists(), "snapshot must be removed after the review"


async def test_run_repo_review_broadcasts_report_link_on_completion(fake_app, mocker):
    """After the graph finishes, push a brief 'review complete' chip into
    the chat with the URL where the full report lives."""
    broadcast_mock = mocker.spy(fake_app.state.chat_hub, "broadcast")

    await _run_repo_review(
        fake_app,
        repo_url="https://github.com/o/r",
        ref="main",
        scope=[],
        thread_id="tid-y",
    )

    # At least one broadcast happened and it points at the report endpoint.
    broadcast_calls = broadcast_mock.await_args_list
    assert broadcast_calls, "expected hub.broadcast to be called after graph completion"
    payloads = [c.args[1] for c in broadcast_calls]
    texts = [p.get("text", "") for p in payloads]
    assert any("/reports/tid-y.md" in t for t in texts), texts


async def test_run_repo_review_broadcasts_progress_per_node(fake_app, mocker):
    """For every node update streamed from the graph, broadcast a
    `{"type": "progress", "node": <name>}` envelope so the UI can light up
    the matching circle in the workflow diagram.

    The frontend distinguishes these from chat messages by inspecting
    `data.type === "progress"`.
    """
    async def fake_astream(state, config=None):
        for name in ("validate_request", "dependency_review", "publish_report"):
            yield {name: {}}

    fake_app.state.graph.astream = fake_astream
    broadcast_mock = mocker.spy(fake_app.state.chat_hub, "broadcast")

    await _run_repo_review(
        fake_app,
        repo_url="https://github.com/o/r",
        ref="main",
        scope=[],
        thread_id="tid-p",
    )

    payloads = [c.args[1] for c in broadcast_mock.await_args_list]
    progress = [p for p in payloads if isinstance(p, dict) and p.get("type") == "progress"]
    nodes = [p["node"] for p in progress]
    # `clone_repo` (active → fired) bracket the pre-graph clone step, then
    # the streamed graph nodes, then the terminal `__done__` envelope (the
    # latter tells the UI to gray out any circle that never fired).
    assert nodes == [
        "clone_repo",
        "clone_repo",
        "validate_request",
        "dependency_review",
        "publish_report",
        "__done__",
    ]


async def test_progress_envelope_for_validate_request_carries_accepted_flag(fake_app):
    """`validate_request` is the branch point between the security-reviewer
    fan-out and `notify_rejection`. The UI needs to colour the rejection
    circle red OR gray the instant validation finishes — not wait until
    the whole graph closes. To do that the progress envelope for the
    `validate_request` chunk carries an `accepted: bool` extracted from
    the validator's `ValidationVerdict`."""
    from src.graph.state import ValidationVerdict

    async def fake_astream(state, config=None):
        # First chunk: validator accepted.
        yield {"validate_request": {"validation": ValidationVerdict(
            accepted=True, category="accepted", reason="ok",
        )}}

    fake_app.state.graph.astream = fake_astream
    from src.chat.progress_store import ProgressStore

    fake_app.state.progress_store = ProgressStore()

    await _run_repo_review(
        fake_app,
        repo_url="https://github.com/o/r",
        ref="main",
        scope=[],
        thread_id="tid-acc",
    )

    events = fake_app.state.progress_store.get("tid-acc")
    validate = next(e for e in events if e["node"] == "validate_request")
    assert validate.get("accepted") is True

    # And the reject branch:
    async def fake_astream_reject(state, config=None):
        yield {"validate_request": {"validation": ValidationVerdict(
            accepted=False, category="trolling", reason="nope",
        )}}

    fake_app.state.graph.astream = fake_astream_reject
    fake_app.state.progress_store = ProgressStore()
    await _run_repo_review(
        fake_app,
        repo_url="https://github.com/o/r",
        ref="main",
        scope=[],
        thread_id="tid-rej",
    )
    events = fake_app.state.progress_store.get("tid-rej")
    validate = next(e for e in events if e["node"] == "validate_request")
    assert validate.get("accepted") is False


async def test_progress_envelope_carries_status_per_node(fake_app):
    """Each progress envelope is tagged with a status the UI can colour-map:
      `fired`    — node returned a non-empty update (green)
      `empty`    — node returned `{}` or nothing meaningful (gray)
      `rejected` — `notify_rejection` always means the review was rejected (red)
    """

    async def fake_astream(state, config=None):
        yield {"validate_request": {"validation": object()}}  # real work
        yield {"injection_review": {}}                         # scope-skipped → empty
        yield {"notify_rejection": {"pr_comment_id": 1}}       # rejection path

    fake_app.state.graph.astream = fake_astream
    from src.chat.progress_store import ProgressStore

    fake_app.state.progress_store = ProgressStore()

    await _run_repo_review(
        fake_app,
        repo_url="https://github.com/o/r",
        ref="main",
        scope=[],
        thread_id="tid-st",
    )

    events = fake_app.state.progress_store.get("tid-st")
    by_node = {e["node"]: e for e in events if e["node"] != "__done__"}
    assert by_node["validate_request"]["status"] == "fired"
    assert by_node["injection_review"]["status"] == "empty"
    assert by_node["notify_rejection"]["status"] == "rejected"


async def test_run_repo_review_appends_progress_events_to_store(fake_app):
    """Progress events are mirrored into `app.state.progress_store` so a WS
    client that connects mid-review can replay the workflow state on connect.

    Without this, the client misses early events fired before the WebSocket
    handshake completes (the BackgroundTask runs immediately after the 202).
    """
    from src.chat.progress_store import ProgressStore

    async def fake_astream(state, config=None):
        for name in ("validate_request", "dependency_review", "publish_report"):
            yield {name: {}}

    fake_app.state.graph.astream = fake_astream
    fake_app.state.progress_store = ProgressStore()

    await _run_repo_review(
        fake_app,
        repo_url="https://github.com/o/r",
        ref="main",
        scope=[],
        thread_id="tid-s",
    )

    events = fake_app.state.progress_store.get("tid-s")
    nodes = [e["node"] for e in events]
    assert nodes == [
        "clone_repo",
        "clone_repo",
        "validate_request",
        "dependency_review",
        "publish_report",
        "__done__",
    ]
    assert all(e["type"] == "progress" for e in events)
