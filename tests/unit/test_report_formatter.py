"""ReportFormatter — optional LLM-copywriter that splices a `### TL;DR`
into the deterministic report body produced by `aggregate_results`.

Contract:
  * Default-OFF: `settings.ENABLE_REPORT_FORMATTER=False` → `run()` returns
    `{}` (no state mutation). No LLM call is made.
  * When ON and there ARE findings: LLM is asked for 2-3 paragraphs of
    plain prose; the result is spliced into `state.final_report` via
    `ReportRenderer.splice_tldr` and also stored in `state.report_tldr`
    so `finalize_exploits` can re-splice after the exploit-loop re-render.
  * Fail-soft: ANY error (LLM raises, empty response, oversized response)
    → return `{}`. The deterministic report from `aggregate_results`
    stays the published version. The agent never blocks publishing.
  * Skip-when-empty: 0 findings across all reviewers → no TL;DR, no LLM
    call. The deterministic "no findings" rendering is already clear.
"""

from unittest.mock import patch

import pytest

from src.agents.report_formatter import ReportFormatter
from src.graph.state import AgentReview, ReviewRequest, ReviewState

_DETERMINISTIC = """## Security review

**Pull request:** https://github.com/o/r/pull/1

**Overall severity:** critical

### Summary

| Severity | Injection | Total |
|----------|-----------|-------|
| Critical | 1 | 1 |

### Critical findings (1)

- [injection] src/api/users.py:42 — sqli — [critical] SQL via f-string

### Injection — critical

summary for injection

- src/api/users.py:42 — sqli — [critical] SQL via f-string
"""


def _review(role: str, severity: str, findings=None) -> AgentReview:
    return AgentReview(
        agent_name=f"{role.title()}Reviewer",
        role=role,
        findings=findings or [],
        summary=f"summary for {role}",
        severity=severity,
        passed=severity in ("info", "minor"),
    )


@pytest.fixture
def state_with_finding() -> ReviewState:
    return ReviewState(
        request=ReviewRequest(pr_url="https://github.com/o/r/pull/1"),
        thread_id="tid-1",
        agent_reviews=[
            _review("injection", "critical", [
                {"file": "src/api/users.py", "line": 42, "category": "sqli",
                 "issue": "SQL via f-string", "severity": "critical"},
            ]),
        ],
        final_report=_DETERMINISTIC,
    )


def _mock_llm(mocker, content: str):
    """Patch `ModelFactory.get` to return an AsyncMock model whose
    `ainvoke(prompt)` resolves to a response with `.content`."""
    mock_model = mocker.AsyncMock()
    mock_response = mocker.AsyncMock()
    mock_response.content = content
    mock_model.ainvoke = mocker.AsyncMock(return_value=mock_response)
    return mock_model


# ── default-off ───────────────────────────────────────────────────────────


async def test_run_returns_empty_update_when_disabled(state_with_finding, mocker):
    """Flag off — formatter is a no-op even when state has findings.
    Crucially, no LLM call is made."""
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", False)
    factory_get = mocker.patch(
        "src.models.factory.ModelFactory.get", return_value=_mock_llm(mocker, "x")
    )
    fmt = ReportFormatter()
    result = await fmt.run(state_with_finding)
    assert result == {}
    # No call at construction time, no call at run time.
    factory_get.assert_not_called()


# ── happy path ────────────────────────────────────────────────────────────


async def test_run_splices_tldr_when_enabled_and_findings_exist(state_with_finding, mocker):
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", True)
    mock_model = _mock_llm(
        mocker,
        "The review found one critical SQL injection in src/api/users.py:42. "
        "Fix before merge.",
    )
    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        fmt = ReportFormatter()
        result = await fmt.run(state_with_finding)
    assert "report_tldr" in result
    assert "SQL injection" in result["report_tldr"]
    new_report = result["final_report"]
    pos_severity = new_report.find("**Overall severity:**")
    pos_tldr = new_report.find("### TL;DR")
    pos_summary = new_report.find("### Summary")
    assert -1 < pos_severity < pos_tldr < pos_summary
    assert "SQL injection" in new_report


# ── fail-soft ─────────────────────────────────────────────────────────────


async def test_run_fail_soft_on_llm_exception(state_with_finding, mocker):
    """LLM raises → formatter swallows the error, returns {}; deterministic
    report from aggregate_results stays the published version. The graph
    keeps going."""
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", True)
    mock_model = mocker.AsyncMock()
    mock_model.ainvoke = mocker.AsyncMock(side_effect=RuntimeError("upstream down"))
    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        fmt = ReportFormatter()
        result = await fmt.run(state_with_finding)
    assert result == {}


async def test_run_fail_soft_on_empty_response(state_with_finding, mocker):
    """LLM returns empty string → don't splice an empty TL;DR block."""
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", True)
    mock_model = _mock_llm(mocker, "")
    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        fmt = ReportFormatter()
        result = await fmt.run(state_with_finding)
    assert result == {}


async def test_run_fail_soft_on_oversized_response(state_with_finding, mocker):
    """LLM goes wild and emits a 10k-char essay — almost certainly
    hallucinating. Drop it rather than publish unverifiable prose."""
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", True)
    mock_model = _mock_llm(mocker, "x" * 10_000)
    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        fmt = ReportFormatter()
        result = await fmt.run(state_with_finding)
    assert result == {}


# ── skip when nothing to summarise ────────────────────────────────────────


async def test_run_skips_when_no_findings(mocker):
    """All reviewers produced 0 findings → TL;DR adds noise, not value.
    Skip the LLM call entirely (and the cost)."""
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", True)
    mock_model = _mock_llm(mocker, "should not be called")
    state = ReviewState(
        request=ReviewRequest(pr_url="https://github.com/o/r/pull/1"),
        thread_id="tid-1",
        agent_reviews=[_review("injection", "info"), _review("owasp", "info")],
        final_report="## Security review\n\n**Overall severity:** info\n",
    )
    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        fmt = ReportFormatter()
        result = await fmt.run(state)
    assert result == {}
    mock_model.ainvoke.assert_not_called()


# ── prompt safety ─────────────────────────────────────────────────────────


async def test_prompt_carries_safety_rules_and_findings(state_with_finding, mocker):
    """The LLM prompt must explicitly forbid invention (files, CVE ids,
    severity changes) AND include the findings the LLM is allowed to
    summarise. Both are guardrails against the failure modes observed
    with small models on this codebase (see
    docs/observed-quality-cases/gpt-oss-20b-2026-06-05.md)."""
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", True)
    mock_model = _mock_llm(mocker, "ok")
    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        fmt = ReportFormatter()
        await fmt.run(state_with_finding)
    prompt = mock_model.ainvoke.await_args.args[0].lower()
    # Safety rules
    assert "do not invent" in prompt or "do not add" in prompt
    assert "do not change" in prompt or "do not alter" in prompt or "do not modify" in prompt
    # Findings reach the prompt verbatim — we want the LLM to ground its
    # summary on real data, not its priors.
    assert "src/api/users.py" in mock_model.ainvoke.await_args.args[0]
    assert "sqli" in mock_model.ainvoke.await_args.args[0]
