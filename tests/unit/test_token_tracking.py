"""LLM token-usage accounting per graph node.

Every LLM call ultimately fires `on_llm_end` on every CallbackHandler
attached to the model. `TokenUsageHandler` reads the current node name
from a ContextVar (`_current_node`) that `timed_node` sets around each
graph-node invocation, and accumulates per-node input/output token
counts + the set of models used.

Why ContextVar instead of inspecting the langgraph callback's `tags`
or `parent_run_id`: the ContextVar copies into every asyncio task
spawned under the node (including the per-file LLM fanout inside
LLMPerFileReviewer). It's the simplest mechanism that survives
`asyncio.gather` without changes to LangGraph plumbing.
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from src.utils.token_tracking import (
    TokenUsageHandler,
    _current_node,
    set_current_node,
)


def _ai_message(input_tokens: int, output_tokens: int, model: str = "claude-sonnet-4-6") -> AIMessage:
    """Build an AIMessage with `usage_metadata` populated like a real
    Anthropic/OpenAI response would have it."""
    return AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata={"model_name": model},
    )


def _llm_result(*messages: AIMessage) -> LLMResult:
    """Wrap one or more AIMessages into an LLMResult — the shape LC
    passes to `on_llm_end`."""
    return LLMResult(generations=[[ChatGeneration(message=m)] for m in messages])


def test_handler_accumulates_per_node_via_contextvar():
    """`on_llm_end` reads `_current_node`; usage groups under it."""
    handler = TokenUsageHandler()

    token = set_current_node("validate_request")
    try:
        handler.on_llm_end(_llm_result(_ai_message(120, 30)))
        handler.on_llm_end(_llm_result(_ai_message(80, 20)))
    finally:
        _current_node.reset(token)

    token = set_current_node("injection_review")
    try:
        handler.on_llm_end(_llm_result(_ai_message(5_000, 200)))
    finally:
        _current_node.reset(token)

    assert handler.usage["validate_request"]["input"] == 200
    assert handler.usage["validate_request"]["output"] == 50
    assert handler.usage["validate_request"]["calls"] == 2
    assert handler.usage["injection_review"]["input"] == 5_000
    assert handler.usage["injection_review"]["output"] == 200
    assert handler.usage["injection_review"]["calls"] == 1


def test_handler_tracks_model_names_per_node():
    """Each node's bucket records the distinct set of model names used."""
    handler = TokenUsageHandler()

    token = set_current_node("dependency_review")
    try:
        handler.on_llm_end(_llm_result(_ai_message(100, 20, model="claude-sonnet-4-6")))
        handler.on_llm_end(_llm_result(_ai_message(150, 30, model="claude-sonnet-4-6")))
        handler.on_llm_end(_llm_result(_ai_message(200, 40, model="qwen-2.5-32b")))
    finally:
        _current_node.reset(token)

    models = handler.usage["dependency_review"]["models"]
    assert "claude-sonnet-4-6" in models
    assert "qwen-2.5-32b" in models
    assert len(models) == 2  # deduped


def test_handler_falls_back_to_llm_output_when_usage_metadata_missing():
    """Older / non-standard providers don't populate `usage_metadata`
    on the message. The handler falls back to `LLMResult.llm_output`."""
    handler = TokenUsageHandler()
    bare_msg = AIMessage(content="ok")  # no usage_metadata
    result = LLMResult(
        generations=[[ChatGeneration(message=bare_msg)]],
        llm_output={
            "token_usage": {"prompt_tokens": 99, "completion_tokens": 11},
            "model_name": "legacy-model",
        },
    )

    token = set_current_node("owasp_review")
    try:
        handler.on_llm_end(result)
    finally:
        _current_node.reset(token)

    assert handler.usage["owasp_review"]["input"] == 99
    assert handler.usage["owasp_review"]["output"] == 11
    assert "legacy-model" in handler.usage["owasp_review"]["models"]


def test_handler_attributes_to_unknown_when_contextvar_absent():
    """LLM calls fired outside any timed_node scope (e.g. lifespan
    init, ad-hoc smoke tests) land in `_unknown` rather than getting
    silently dropped."""
    handler = TokenUsageHandler()
    handler.on_llm_end(_llm_result(_ai_message(50, 5)))

    assert handler.usage["_unknown"]["input"] == 50
    assert handler.usage["_unknown"]["output"] == 5


def test_handler_totals_helper():
    """Convenience: `.totals()` rolls up `input` / `output` / `calls`
    across all nodes. The UI's "Token usage: X in / Y out" line reads
    from here without re-summing on the client side."""
    handler = TokenUsageHandler()

    for node, (input_t, output_t) in [
        ("validate_request", (200, 30)),
        ("dependency_review", (1500, 80)),
        ("injection_review", (4000, 150)),
    ]:
        token = set_current_node(node)
        try:
            handler.on_llm_end(_llm_result(_ai_message(input_t, output_t)))
        finally:
            _current_node.reset(token)

    totals = handler.totals()
    assert totals["input"] == 200 + 1500 + 4000
    assert totals["output"] == 30 + 80 + 150
    assert totals["calls"] == 3


def test_contextvar_propagates_to_asyncio_task_children():
    """LLMPerFileReviewer fans out per-file LLM calls via asyncio.gather.
    The child tasks must inherit the `_current_node` value so usage
    accumulates against the reviewer, not `_unknown`. Python's asyncio
    copies the current context on task creation, so this should work —
    pin it with a test so future code can't accidentally `set_current_node`
    in a child and break attribution."""

    handler = TokenUsageHandler()

    async def child_llm_call():
        handler.on_llm_end(_llm_result(_ai_message(100, 20)))

    async def runner():
        token = set_current_node("injection_review")
        try:
            await asyncio.gather(child_llm_call(), child_llm_call(), child_llm_call())
        finally:
            _current_node.reset(token)

    asyncio.run(runner())

    assert handler.usage["injection_review"]["calls"] == 3
    assert handler.usage["injection_review"]["input"] == 300
    assert handler.usage["injection_review"]["output"] == 60
