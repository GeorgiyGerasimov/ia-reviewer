"""Per-graph-node LLM token-usage accounting.

Surfaces three things in the review summary:
  * total input + output tokens spent on the review,
  * a per-node breakdown (which agent / step burned what),
  * which model(s) were called for each node.

Mechanism:
  1. `_current_node` is a `ContextVar[str]` set by `timed_node` around
     every graph-node invocation (and explicitly by the on-demand
     exploit endpoint).
  2. `TokenUsageHandler` is a LangChain `BaseCallbackHandler` registered
     via `RunnableConfig.callbacks` alongside the Langfuse handler.
     On every `on_llm_end` it reads the ContextVar and accumulates the
     call's usage_metadata into a per-node bucket.

The handler is created fresh per request (per `_trace_config` call),
populated during graph execution, then drained into `state.token_usage`
which goes to the DB and the `/reviews/{tid}` response.

Why ContextVar — Python's asyncio copies the current context when a
task is created, so `_current_node` propagates correctly to per-file
LLM fanouts via `asyncio.gather` (tested in
`tests/unit/test_token_tracking.py`). No threading involved; this is
async-only code.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

# Set by `timed_node` (and by the exploit endpoint) so every LLM call
# fired inside the wrapped function gets attributed to the correct node.
# Default `None` → falls back to the `_unknown` bucket so misattributed
# usage is visible rather than silently dropped.
_current_node: ContextVar[str | None] = ContextVar("current_node", default=None)


def set_current_node(name: str | None) -> Token:
    """Set the current-node ContextVar; returns the reset token. Wrap
    your call site in try/finally to reset cleanly even on exceptions:

        token = set_current_node("validate_request")
        try:
            ...
        finally:
            _current_node.reset(token)
    """
    return _current_node.set(name)


def get_current_node() -> str | None:
    """Read the current node — useful for tests + debug logs."""
    return _current_node.get()


# Bucket the handler stores per node. Kept open-ended (dict) so the
# wire format is stable as we add fields later (e.g. cost, model rank).
_EMPTY_BUCKET = {"input": 0, "output": 0, "calls": 0, "models": []}


class TokenUsageHandler(BaseCallbackHandler):
    """LangChain callback handler that accumulates token usage per
    graph node (read from `_current_node`).

    Usage shape (the JSON we persist + return to the UI):

        {
          "validate_request": {
            "input": 1234,
            "output": 56,
            "calls": 1,
            "models": ["claude-sonnet-4-6"],
          },
          "injection_review": {
            "input": 8000,
            "output": 200,
            "calls": 3,
            "models": ["claude-sonnet-4-6"],
          },
          ...
        }

    Fail-soft: any extraction error (malformed response, missing
    metadata, unknown provider shape) lands the call in the bucket
    with zero tokens but still increments `calls`. Never raises.
    """

    def __init__(self) -> None:
        self.usage: dict[str, dict] = {}

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:  # noqa: D401 — LC API
        node = _current_node.get() or "_unknown"
        bucket = self._bucket(node)
        try:
            in_tokens, out_tokens, model = _extract_usage(response)
        except Exception:
            # Truly defensive — a provider with an unexpected shape
            # shouldn't break the review.
            in_tokens, out_tokens, model = 0, 0, None
        bucket["input"] += in_tokens
        bucket["output"] += out_tokens
        bucket["calls"] += 1
        if model and model not in bucket["models"]:
            bucket["models"].append(model)

    def totals(self) -> dict:
        """Roll up all buckets into a single {input, output, calls} dict.
        The summary panel reads this for the headline "X in / Y out" line
        without having to sum on the client side."""
        total_in = sum(b["input"] for b in self.usage.values())
        total_out = sum(b["output"] for b in self.usage.values())
        total_calls = sum(b["calls"] for b in self.usage.values())
        return {"input": total_in, "output": total_out, "calls": total_calls}

    def _bucket(self, node: str) -> dict:
        existing = self.usage.get(node)
        if existing is None:
            existing = {"input": 0, "output": 0, "calls": 0, "models": []}
            self.usage[node] = existing
        return existing


# ── extraction helpers ──────────────────────────────────────────────


def _extract_usage(response: Any) -> tuple[int, int, str | None]:
    """Pull `(input, output, model)` out of an `LLMResult`.

    Two probes:
      1. Modern LangChain (>= 0.2): `generations[i][j].message.usage_metadata`
         — provider-agnostic, populated by Anthropic / OpenAI / Google.
      2. Legacy fallback: `LLMResult.llm_output["token_usage"]` — older
         shape some local-model adapters still emit.

    Model name probes in the same order:
      1. `message.response_metadata.model_name` / `.model`
      2. `llm_output.model_name` / `.model`
    """
    in_tokens = 0
    out_tokens = 0
    model: str | None = None

    generations = getattr(response, "generations", None) or []
    for gen_batch in generations:
        for gen in gen_batch:
            msg = getattr(gen, "message", None)
            if msg is None:
                continue
            usage = getattr(msg, "usage_metadata", None)
            if usage:
                in_tokens += int(usage.get("input_tokens") or 0)
                out_tokens += int(usage.get("output_tokens") or 0)
            if model is None:
                meta = getattr(msg, "response_metadata", None) or {}
                model = meta.get("model_name") or meta.get("model")

    if in_tokens == 0 and out_tokens == 0:
        # Fallback to the legacy llm_output shape (some local model
        # adapters route their usage there instead of usage_metadata).
        llm_output = getattr(response, "llm_output", None) or {}
        usage = llm_output.get("token_usage") or {}
        in_tokens = int(usage.get("prompt_tokens") or 0)
        out_tokens = int(usage.get("completion_tokens") or 0)
        if model is None:
            model = llm_output.get("model_name") or llm_output.get("model")
    elif model is None:
        # Even when generations carried tokens, model name can live on
        # llm_output (some providers). Last-ditch lookup.
        llm_output = getattr(response, "llm_output", None) or {}
        model = llm_output.get("model_name") or llm_output.get("model")

    return in_tokens, out_tokens, model
