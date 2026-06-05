"""`BaseReviewer._parse_response` strips thinking traces before parsing.

When the LLM is a reasoning model (Qwen3.6-27B, DeepSeek R1, o1-style),
its raw response contains a long chain-of-thought trace before the
actual JSON answer, fenced by `</think>`. Without stripping, our
`json.loads` falls through to the raw-fallback path and every per-file
call silently produces zero findings — the worst kind of failure.

Contract:
  * Pure-prose thinking + `</think>` + JSON → parses the JSON
  * Plain JSON without thinking → unchanged behaviour (back-compat)
  * `</think>` with no parseable JSON after → falls through to the
    pre-existing raw-fallback path (passed=True, severity=info)
"""

from src.agents.base_reviewer import LLMPerFileReviewer


class _Concrete(LLMPerFileReviewer):
    """Minimal subclass — we only need `_parse_response`, no actual LLM."""
    role = "injection"
    PATH_PATTERNS = ("*.py",)
    prompt_template = "ignored"

    def __init__(self):
        # Skip BaseReviewer.__init__ — we don't need a ModelFactory build
        self.agent_name = "test"
        self.model_name = "test"


def test_parse_response_strips_qwen_style_thinking():
    """Realistic Qwen3.6-27B response shape — long thinking trace
    terminated by `</think>` then the actual JSON."""
    r = _Concrete()
    response = (
        "Here's a thinking process:\n"
        "1. The code uses an f-string for SQL\n"
        "2. This is a classic SQLi\n"
        "3. Need to report critical severity\n"
        "</think>\n\n"
        '{"findings": [{"file": "a.py", "issue": "sqli via f-string", '
        '"severity": "critical"}], "summary": "1 critical SQLi found", '
        '"severity": "critical"}'
    )
    result = r._parse_response(response)
    assert len(result["findings"]) == 1
    assert result["findings"][0]["file"] == "a.py"
    assert result["severity"] == "critical"
    assert result["passed"] is False
    assert "1 critical SQLi found" in result["summary"]


def test_parse_response_plain_json_still_works():
    """Back-compat — when there is no thinking trace, behave exactly as before."""
    r = _Concrete()
    response = (
        '{"findings": [{"issue": "weak hash"}], '
        '"summary": "minor", "severity": "minor"}'
    )
    result = r._parse_response(response)
    assert len(result["findings"]) == 1
    assert result["severity"] == "minor"


def test_parse_response_fenced_json_after_thinking_still_works():
    """Some models emit thinking trace, then a ```json fenced block.
    Strip should leave the fenced block intact so the existing
    `_JSON_BLOCK_RE` regex can find it."""
    r = _Concrete()
    response = (
        "let me think...\n</think>\n\n"
        "Sure! Here is the analysis:\n\n"
        '```json\n{"findings": [], "summary": "clean", "severity": "info"}\n```'
    )
    result = r._parse_response(response)
    assert result["findings"] == []
    assert result["severity"] == "info"
    assert result["passed"] is True


def test_parse_response_thinking_with_no_json_falls_through_softly():
    """If the model only emits thinking and never gets to a parseable
    JSON, the existing raw-fallback kicks in (passed=True, raw stored).
    Reviewer keeps going, no exception. Crucially the raw payload is the
    STRIPPED prose (the bit after `</think>`), not the full 1000-token
    thinking trace — otherwise the report would be polluted with reasoning
    noise instead of just the model's bottom-line statement."""
    r = _Concrete()
    response = "lots of musing\n</think>\nI cannot decide."
    result = r._parse_response(response)
    # raw fallback contract — pre-existing behaviour, with stripped raw.
    assert result["passed"] is True
    assert result["severity"] == "info"
    assert result["findings"][0]["raw"] == "I cannot decide."
    # The thinking trace itself must NOT leak into the report fallback.
    assert "lots of musing" not in result["findings"][0]["raw"]
