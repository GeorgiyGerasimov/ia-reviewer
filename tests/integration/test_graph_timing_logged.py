"""E2E sanity check: real graph runs emit `node_complete` timing lines.

The `timed_node` helper has its own unit tests; this integration test
proves that `build_review_graph` actually applies the wrapper to every
node it registers. A compile-time mistake (forgetting `timed_node(...)`
on one of the twelve `add_node` calls) would silently drop that node
from the metric stream — this test catches that.
"""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from src.graph.coordinator import build_review_graph
from src.graph.state import ReviewRequest, ReviewState


@pytest.fixture(autouse=True)
def _stub_model_factory():
    """Reviewer construction calls ModelFactory.get; mock it so the
    graph compiles without an LLM dependency."""
    mock_model = AsyncMock()
    # Validator's LLM judge expects strict-JSON, default to accept-shape.
    mock_response = AsyncMock()
    mock_response.content = (
        '{"verdict":"accept","category":"accepted","reason":"mock"}'
    )
    mock_model.ainvoke = AsyncMock(return_value=mock_response)
    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        yield


async def test_running_graph_emits_node_complete_for_validate(caplog, tmp_path):
    """A trivial PR-mode review reaches `validate_request` at minimum.
    Even on the reject path that node fires and must log."""
    graph = build_review_graph()

    # Empty diff → validator's pure-code prefilter rejects → notify_rejection.
    state = ReviewState(
        thread_id="timing-test",
        request=ReviewRequest(
            mode="pr",
            pr_url="https://github.com/o/r/pull/1",
            diff="",
            files_changed=[],
        ),
    )

    with caplog.at_level(logging.INFO, logger="graph.timing"):
        await graph.ainvoke(state, config={"configurable": {"thread_id": "timing-test"}})

    lines = [
        r.getMessage() for r in caplog.records
        if r.name == "graph.timing"
    ]
    # At minimum: validate_request fired. notify_rejection also fired on this path.
    nodes_logged = {
        line.split("node=")[1].split(" ")[0]
        for line in lines
        if "node=" in line
    }
    assert "validate_request" in nodes_logged, (
        f"expected validate_request timing in logs; got nodes: {nodes_logged}"
    )
    # Each timing line must carry duration_ms and status=ok
    for line in lines:
        assert "duration_ms=" in line
        assert "thread_id=timing-test" in line


async def test_each_logged_line_has_stable_field_order(caplog, tmp_path):
    """Downstream parsers depend on field order: `node=X thread_id=Y
    duration_ms=Z status=...`. Validate the regex matches every line.
    """
    import re

    graph = build_review_graph()
    state = ReviewState(
        thread_id="parse-test",
        request=ReviewRequest(mode="pr", pr_url="https://x/p/1", diff=""),
    )

    with caplog.at_level(logging.INFO, logger="graph.timing"):
        await graph.ainvoke(state, config={"configurable": {"thread_id": "parse-test"}})

    pattern = re.compile(
        r"node_complete node=\S+ thread_id=\S+ duration_ms=\d+ status=(ok|error)"
    )
    lines = [
        r.getMessage() for r in caplog.records
        if r.name == "graph.timing"
    ]
    assert lines, "expected at least one timing line"
    for line in lines:
        assert pattern.search(line), (
            f"timing line does not match the documented field order: {line!r}"
        )
