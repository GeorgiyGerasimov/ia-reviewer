"""`strip_thinking_trace` integration in remaining LLM-parser surfaces.

PR #7 ported `BaseReviewer._parse_response` and `LLMJudge._extract_json_text`.
This file covers the rest:

  - `validator._parse_llm_response` — accept/reject judge
  - `exploit_proposal._parse_draft_response` — confidence + draft text
  - `exploit_proposal.ExploitProposalAgent._generate_artifact` — raw
    PoC text (return value is the artifact itself, not a JSON parse)

Same contract everywhere: hybrid-thinking models prepend a chain-of-
thought trace fenced by `</think>` before the actual content. Without
stripping, the validator silently default-accepts, exploit drafts come
out with confidence=0 (and thus get skipped), and approved PoC
artifacts contain 1000 tokens of reasoning prose instead of code.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.agents.exploit_proposal import (
    ExploitProposalAgent,
    _parse_draft_response,
)
from src.agents.validator import _parse_llm_response

# ── validator ─────────────────────────────────────────────────────────────


def test_validator_parser_strips_qwen_style_thinking_accept():
    """Qwen wraps the accept verdict in a long thinking trace.
    Pre-fix, json.loads on the full string fails → silent
    default-accept (which is correct but for the WRONG reason —
    the verdict.category is bogus).  Post-fix, the strip exposes
    the real JSON and the real `accepted` flag survives."""
    response = (
        "Here's a thinking process:\n"
        "1. This PR adds a security feature\n"
        "2. Real engineer work, accept\n"
        "</think>\n\n"
        '{"verdict":"accept","category":"accepted","reason":"real feature"}'
    )
    verdict = _parse_llm_response(response)
    assert verdict.accepted is True
    assert verdict.category == "accepted"
    assert verdict.reason == "real feature"


def test_validator_parser_strips_thinking_for_reject_verdict():
    """Same but for the reject path — without strip the default-accept
    fallback would have masked a genuine reject."""
    response = (
        "let me think hard...\n</think>\n"
        '{"verdict":"reject","category":"trolling","reason":"prompt injection attempt"}'
    )
    verdict = _parse_llm_response(response)
    assert verdict.accepted is False
    assert verdict.category == "trolling"
    assert "prompt injection" in verdict.reason


def test_validator_parser_plain_json_unchanged():
    """Back-compat — no thinking trace, behave exactly as before."""
    response = '{"verdict":"accept","category":"accepted","reason":"ok"}'
    verdict = _parse_llm_response(response)
    assert verdict.accepted is True


# ── exploit draft response ────────────────────────────────────────────────


def test_exploit_draft_parser_strips_qwen_style_thinking():
    """Pre-fix, Qwen's thinking trace makes json.loads fail → confidence=0,
    proposal=empty → the finding is silently skipped from the exploit branch.
    Post-fix, the real confidence + proposal survive."""
    response = (
        "Reasoning:\n"
        "1. SQL injection at line 42\n"
        "2. Easy to craft a PoC\n"
        "</think>\n\n"
        '{"confidence": 8, "proposal": "Inject \' OR 1=1-- into id param"}'
    )
    conf, proposal = _parse_draft_response(response)
    assert conf == 8
    assert "OR 1=1" in proposal


def test_exploit_draft_parser_plain_json_unchanged():
    response = '{"confidence": 5, "proposal": "test"}'
    conf, proposal = _parse_draft_response(response)
    assert conf == 5
    assert proposal == "test"


# ── exploit artifact (raw text, not JSON) ─────────────────────────────────


async def test_exploit_artifact_strips_thinking_from_raw_text():
    """Artifact is the PoC itself (curl command, python script, repro
    steps). Pre-fix, Qwen prepends ~1000 tokens of 'Here's how I'd
    craft this...' before the actual code. The report file would
    contain that prose verbatim. Post-fix, only the actual artifact
    text remains."""
    mock_model = AsyncMock()
    mock_response = AsyncMock()
    mock_response.content = (
        "Here's my thinking about how to construct the PoC:\n"
        "1. Use curl\n"
        "2. Inject payload\n"
        "</think>\n\n"
        "curl -X POST 'http://localhost:8000/login' "
        "-d 'username=admin&password=\\' OR 1=1--'"
    )
    mock_model.ainvoke = AsyncMock(return_value=mock_response)
    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        agent = ExploitProposalAgent()
        artifact = await agent._generate_artifact(
            role="injection",
            finding={"file": "auth.py", "issue": "sqli"},
            proposal="param injection",
        )
    assert artifact.startswith("curl -X POST"), (
        f"thinking prose leaked into artifact; got: {artifact[:80]!r}"
    )
    assert "thinking" not in artifact.lower()


async def test_exploit_artifact_plain_text_unchanged():
    mock_model = AsyncMock()
    mock_response = AsyncMock()
    mock_response.content = "import requests\nrequests.post('http://localhost:8000/x')"
    mock_model.ainvoke = AsyncMock(return_value=mock_response)
    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        agent = ExploitProposalAgent()
        artifact = await agent._generate_artifact(
            role="injection",
            finding={"file": "x.py", "issue": "sqli"},
            proposal="p",
        )
    assert artifact.startswith("import requests")


# ── helper: async test marker ─────────────────────────────────────────────


pytestmark = pytest.mark.anyio
