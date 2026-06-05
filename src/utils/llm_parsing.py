"""Parser-side utilities for LLM response handling.

Lives here (not in any single agent) because multiple agents parse
LLM output today: `BaseReviewer`, `LLMJudge`, `RequestValidator`,
`ReviewDecisionAgent`, `ExploitProposalAgent`. They all share the
same threat of malformed input — thinking-mode trailers (Qwen3.6-27B,
DeepSeek R1, o1-style), markdown fences, surrounding prose.

Currently exposed:

* `strip_thinking_trace(content)` — defensive removal of
  chain-of-thought trace fenced by `</think>` (and optionally
  preceded by `<think>` or `Here's a thinking process:` prose).
  No-op when the content has no `</think>` marker — safe to call
  unconditionally before strict-JSON parsers.
"""

from __future__ import annotations

_THINK_CLOSE_MARKER = "</think>"


def strip_thinking_trace(content: str) -> str:
    """Remove a chain-of-thought trace from a reasoning-model response.

    Strategy: find the LAST occurrence of `</think>` and return
    everything after it, left-stripped of whitespace/newlines. If no
    marker is present, return the content unchanged.

    Why "after the LAST occurrence" rather than the first:
      - Some models nest `<think>...</think>` blocks
      - Some emit two separate "let me reconsider" traces in one reply
      - The model's FINAL answer always lives after the last close tag
      - Picking the first marker would slice into the model's own
        narrative if it ever (mis-)used `</think>` mid-prose

    Edge cases:
      - Empty input → empty output
      - Pure whitespace (no marker) → returned unchanged (preserves
        existing parser behaviour for "obviously not JSON" content)
      - `</think>` with only whitespace after it → empty string
        (downstream parser will then hit raw-fallback path cleanly)
      - No marker at all → input unchanged (defensive default — many
        responses simply have no thinking trace)
    """
    if not content:
        return content
    last_close = content.rfind(_THINK_CLOSE_MARKER)
    if last_close == -1:
        return content
    tail = content[last_close + len(_THINK_CLOSE_MARKER):]
    return tail.lstrip()
