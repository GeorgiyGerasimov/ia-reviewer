"""`strip_thinking_trace` — defensive parser-side strip for reasoning-model output.

Some hybrid-thinking models (Qwen3.6-27B, DeepSeek R1, o1-style)
prepend a chain-of-thought trace BEFORE the actual JSON/answer, fenced
by `</think>` (and sometimes opened by `<think>`). Our strict-JSON
parsers in `BaseReviewer` and `LLMJudge` choke on that prose unless
the trace is stripped first.

Contract:
  * No `</think>` marker → return content unchanged
  * `</think>` present → return everything AFTER the LAST occurrence,
    left-stripped of whitespace/newlines
  * Multiple `</think>` markers → take after the LAST one (defensive
    against nested or accidental occurrences)
  * Empty / pure-whitespace content → return empty string
  * `<think>...</think>{json}` form → return `{json}` (only-after-close
    semantics handles both implicit-open and explicit-open thinking)
"""

from src.utils.llm_parsing import strip_thinking_trace

# ── happy paths ────────────────────────────────────────────────────────


def test_plain_json_returned_unchanged():
    body = '{"findings": [], "severity": "info"}'
    assert strip_thinking_trace(body) == body


def test_plain_prose_returned_unchanged():
    body = "no findings"
    assert strip_thinking_trace(body) == body


def test_empty_returns_empty():
    assert strip_thinking_trace("") == ""
    assert strip_thinking_trace("   \n  ") == "   \n  "


# ── thinking trace stripping ───────────────────────────────────────────


def test_simple_close_marker_keeps_after():
    content = "Some reasoning here.\n</think>\n\n{\"findings\": []}"
    assert strip_thinking_trace(content) == '{"findings": []}'


def test_qwen_style_full_trace_stripped():
    """Real shape from Qwen3.6-27B reasoning mode — 'Here's a thinking
    process' header, numbered steps, then `</think>` + blank + JSON."""
    content = (
        "Here's a thinking process:\n\n"
        "1.  **Analyze User Input:**\n"
        "    - Code does X\n"
        "    - This is bad\n"
        "2.  **Formulate Findings:**\n"
        "    - SQL injection at line 42\n"
        "</think>\n\n"
        '{"findings": [{"file": "a.py", "issue": "sqli", "severity": "critical"}]}'
    )
    out = strip_thinking_trace(content)
    assert out.startswith('{"findings"')
    assert "thinking process" not in out
    assert "Analyze User Input" not in out


def test_xml_style_paired_tags_stripped():
    """`<think>...</think>{json}` — handle both opening and closing tag."""
    content = "<think>I should look at this carefully</think>\n{\"ok\": true}"
    assert strip_thinking_trace(content) == '{"ok": true}'


def test_multiple_close_markers_takes_after_last():
    """Defensive: if model emits multiple `</think>` (nested, repeated,
    or genuinely confused), take after the LAST one — that's where the
    final answer should live."""
    content = (
        "first attempt</think>\n"
        "wait, reconsider</think>\n"
        '{"final": "answer"}'
    )
    assert strip_thinking_trace(content) == '{"final": "answer"}'


def test_close_marker_with_only_whitespace_after_returns_empty():
    """`</think>` with nothing useful after → return empty string so the
    downstream parser fails cleanly (and our fail-soft kicks in) rather
    than try to parse a whitespace blob."""
    content = "lots of thinking\n</think>\n   \n"
    assert strip_thinking_trace(content) == ""


def test_close_marker_at_very_end_returns_empty():
    content = "thinking\n</think>"
    assert strip_thinking_trace(content) == ""


# ── integration: parser-friendly output ────────────────────────────────


def test_after_strip_json_parses():
    """Whole point: after stripping, json.loads must succeed on a
    realistic Qwen response."""
    import json

    content = (
        "Reasoning step 1\n"
        "Reasoning step 2\n"
        "</think>\n\n"
        '{"findings": [{"file": "x.py", "severity": "major"}], "summary": "ok"}'
    )
    stripped = strip_thinking_trace(content)
    data = json.loads(stripped)
    assert data["findings"][0]["file"] == "x.py"
    assert data["summary"] == "ok"
