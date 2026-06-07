"""Integration tests: the Langfuse callback flows from `app.state` through
`_trace_config` into the `graph.ainvoke` config on both initial review and
resume paths.
"""

from unittest.mock import MagicMock

import pytest

from main import _resume_review, _run_review, _trace_config, create_test_app
from src.graph.state import ReviewRequest, ReviewState
from src.utils.token_tracking import TokenUsageHandler


def _state() -> ReviewState:
    return ReviewState(
        request=ReviewRequest(
            pr_url="https://github.com/o/r/pull/7",
            diff="d",
            files_changed=["a.py"],
            author="dev",
        )
    )


# ── _trace_config helper ─────────────────────────────────────────────────────


def test_trace_config_attaches_token_handler_even_without_langfuse(mocker):
    """The TokenUsageHandler is unconditional — token accounting works
    whether Langfuse is wired or not. Pin: a `callbacks` list with at
    least one entry, and that entry is a TokenUsageHandler."""
    app = create_test_app(graph=mocker.AsyncMock(), github=mocker.AsyncMock(), langfuse_callback=None)
    config = _trace_config(app, "tid-1", _state(), trigger="http")
    assert config["configurable"]["thread_id"] == "tid-1"
    callbacks = config["callbacks"]
    assert any(isinstance(cb, TokenUsageHandler) for cb in callbacks)


def test_trace_config_includes_handler_and_metadata(mocker):
    handler = MagicMock(name="CallbackHandler")
    app = create_test_app(
        graph=mocker.AsyncMock(),
        github=mocker.AsyncMock(),
        langfuse_callback=handler,
    )
    config = _trace_config(app, "tid-2", _state(), trigger="http")

    # Both the Langfuse handler AND the TokenUsageHandler are registered.
    callbacks = config["callbacks"]
    assert handler in callbacks
    assert any(isinstance(cb, TokenUsageHandler) for cb in callbacks)
    metadata = config["metadata"]
    assert metadata["session_id"] == "tid-2"
    assert metadata["user_id"] == "dev"
    assert metadata["pr_url"] == "https://github.com/o/r/pull/7"
    assert "security-review" in metadata["tags"]
    assert "http" in metadata["tags"]


# ── _run_review / _resume_review send tracing through ────────────────────────


@pytest.fixture
def traced_app(mocker):
    handler = MagicMock(name="CallbackHandler")
    app = create_test_app(
        graph=mocker.AsyncMock(),
        github=mocker.AsyncMock(),
        langfuse_callback=handler,
    )
    app.state.github.fetch_pr = mocker.AsyncMock(
        return_value=ReviewRequest(
            pr_url="https://github.com/o/r/pull/7",
            diff="d",
            files_changed=["a.py"],
            author="dev",
        )
    )
    return app, handler


async def test_run_review_passes_tracing_to_graph_ainvoke(traced_app):
    """Contract: _run_review forwards the Langfuse-enabled config into the
    LangGraph streaming call. (Switched from ainvoke to astream for the
    UI progress-broadcast wiring; the config shape is unchanged.)
    """
    app, handler = traced_app
    captured: list[dict] = []

    async def fake_astream(state, config=None):
        captured.append(config)
        yield {"publish_report": {}}

    app.state.graph.astream = fake_astream

    await _run_review(app, "https://github.com/o/r/pull/7", [], "tid-3")

    assert len(captured) == 1
    config = captured[0]
    # Langfuse handler always rides along; TokenUsageHandler joins.
    assert handler in config["callbacks"]
    assert any(isinstance(cb, TokenUsageHandler) for cb in config["callbacks"])
    assert config["metadata"]["pr_url"] == "https://github.com/o/r/pull/7"
    assert "http" in config["metadata"]["tags"]


async def test_resume_review_passes_tracing_to_graph_ainvoke(traced_app):
    app, handler = traced_app
    # aget_state must return some snapshot for _broadcast_pending_interrupts;
    # an empty-tasks snapshot keeps the post-resume broadcast a no-op.
    empty_snapshot = MagicMock()
    empty_snapshot.tasks = []
    app.state.graph.aget_state.return_value = empty_snapshot

    await _resume_review(app, "tid-4", "approve abc")

    app.state.graph.ainvoke.assert_awaited_once()
    config = app.state.graph.ainvoke.await_args.kwargs["config"]
    assert handler in config["callbacks"]
    assert any(isinstance(cb, TokenUsageHandler) for cb in config["callbacks"])
    assert "resume" in config["metadata"]["tags"]
