"""Judge-gate on the formatter's TL;DR output.

When `settings.FORMATTER_JUDGE_CHECK=True`, the formatter runs every
generated TL;DR through `LLMJudge` against a small rubric (does it cite
only real files? does it invent CVE ids? does it change severities?).
If the judge rejects the TL;DR, the formatter drops it and the
deterministic report from `aggregate_results` is what gets published.

Contract:
  * `FORMATTER_JUDGE_CHECK=False` (default) → no judge call, formatter
    behaves exactly as in PR #3.
  * `FORMATTER_JUDGE_CHECK=True` + judge says `passed=True` → TL;DR
    lands as before.
  * `FORMATTER_JUDGE_CHECK=True` + judge says `passed=False` →
    formatter returns `{}` (deterministic stays).
  * `FORMATTER_JUDGE_CHECK=True` + judge RAISES → fail-CLOSED for the
    gate: formatter returns `{}`. Reasoning: if we can't verify the
    polish is grounded, conservatively don't ship it.
  * The eval doc the judge sees MUST include both the findings (ground
    truth) and the proposed TL;DR (under review) — otherwise the rubric
    has no way to detect invented files / CVEs / severity drift.
"""

from unittest.mock import patch

import pytest

from src.agents.report_formatter import ReportFormatter
from src.evals.llm_judge import CriterionResult, JudgeVerdict
from src.graph.state import AgentReview, ReviewRequest, ReviewState

_DETERMINISTIC = """## Security review

**Pull request:** https://github.com/o/r/pull/1

**Overall severity:** critical

### Summary

| Severity | Injection | Total |
|----------|-----------|-------|
| Critical | 1 | 1 |

### Injection — critical

summary

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


def _mock_model(mocker, content: str):
    mock_model = mocker.AsyncMock()
    mock_response = mocker.AsyncMock()
    mock_response.content = content
    mock_model.ainvoke = mocker.AsyncMock(return_value=mock_response)
    return mock_model


def _fake_judge_returning(mocker, verdict: JudgeVerdict):
    """Build a fake LLMJudge whose evaluate() resolves to a fixed verdict."""
    judge = mocker.AsyncMock()
    judge.evaluate = mocker.AsyncMock(return_value=verdict)
    return judge


def _fake_judge_raising(mocker, exc: Exception):
    judge = mocker.AsyncMock()
    judge.evaluate = mocker.AsyncMock(side_effect=exc)
    return judge


# ── default: gate is off ─────────────────────────────────────────────────


async def test_judge_gate_disabled_does_not_call_judge(state_with_finding, mocker):
    """FORMATTER_JUDGE_CHECK=False → judge.evaluate is never called even
    when formatter is enabled. This is the PR#3 default behaviour."""
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", True)
    mocker.patch("src.agents.report_formatter.settings.FORMATTER_JUDGE_CHECK", False)
    judge = _fake_judge_returning(
        mocker, JudgeVerdict(passed=False, overall_score=0, per_criterion=[], rationale="")
    )
    with patch("src.models.factory.ModelFactory.get", return_value=_mock_model(mocker, "ok tldr")):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        fmt = ReportFormatter(judge=judge)
        result = await fmt.run(state_with_finding)
    # TL;DR landed (gate off — formatter trusts its own output)
    assert "report_tldr" in result
    judge.evaluate.assert_not_called()


# ── gate enabled, judge passes ───────────────────────────────────────────


async def test_judge_gate_enabled_passing_verdict_lets_tldr_through(state_with_finding, mocker):
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", True)
    mocker.patch("src.agents.report_formatter.settings.FORMATTER_JUDGE_CHECK", True)
    judge = _fake_judge_returning(mocker, JudgeVerdict(
        passed=True, overall_score=9, per_criterion=[
            CriterionResult(name="tldr_only_real_files", met=True, evidence="ok"),
        ], rationale="grounded"
    ))
    with patch("src.models.factory.ModelFactory.get",
               return_value=_mock_model(mocker, "Grounded TL;DR text.")):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        fmt = ReportFormatter(judge=judge)
        result = await fmt.run(state_with_finding)
    assert "report_tldr" in result
    assert "Grounded TL;DR text." in result["report_tldr"]
    judge.evaluate.assert_called_once()


# ── gate enabled, judge rejects ──────────────────────────────────────────


async def test_judge_gate_enabled_failing_verdict_drops_tldr(state_with_finding, mocker):
    """Judge says passed=False → formatter returns {} so deterministic
    report stays. Hallucination guardrail in action."""
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", True)
    mocker.patch("src.agents.report_formatter.settings.FORMATTER_JUDGE_CHECK", True)
    judge = _fake_judge_returning(mocker, JudgeVerdict(
        passed=False, overall_score=3, per_criterion=[
            CriterionResult(name="tldr_only_real_files", met=False,
                            evidence="mentions src/auth.py which is not in findings"),
        ], rationale="invented file path"
    ))
    with patch("src.models.factory.ModelFactory.get",
               return_value=_mock_model(mocker, "Hallucinated TL;DR mentioning src/auth.py.")):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        fmt = ReportFormatter(judge=judge)
        result = await fmt.run(state_with_finding)
    assert result == {}
    judge.evaluate.assert_called_once()


# ── gate enabled, judge errors → fail-CLOSED ─────────────────────────────


async def test_judge_gate_enabled_judge_error_fail_closed_drops_tldr(state_with_finding, mocker):
    """If the judge itself raises (network, parser, whatever), we can't
    verify the TL;DR is grounded — conservatively don't ship it.

    This is intentionally MORE strict than the formatter's primary
    fail-soft path: a failed gate is treated as a failed verdict. The
    deterministic report still publishes, so the user never loses
    information."""
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", True)
    mocker.patch("src.agents.report_formatter.settings.FORMATTER_JUDGE_CHECK", True)
    judge = _fake_judge_raising(mocker, RuntimeError("judge unreachable"))
    with patch("src.models.factory.ModelFactory.get",
               return_value=_mock_model(mocker, "Some TL;DR.")):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        fmt = ReportFormatter(judge=judge)
        result = await fmt.run(state_with_finding)
    assert result == {}


# ── prompt grounding ─────────────────────────────────────────────────────


async def test_judge_eval_input_contains_both_findings_and_tldr(state_with_finding, mocker):
    """The document handed to the judge MUST contain both the findings
    (ground truth) and the proposed TL;DR (under review). Without both,
    the judge has no way to tell whether the TL;DR invented files or
    changed severities."""
    mocker.patch("src.agents.report_formatter.settings.ENABLE_REPORT_FORMATTER", True)
    mocker.patch("src.agents.report_formatter.settings.FORMATTER_JUDGE_CHECK", True)
    judge = _fake_judge_returning(mocker, JudgeVerdict(
        passed=True, overall_score=9, per_criterion=[], rationale=""
    ))
    with patch("src.models.factory.ModelFactory.get",
               return_value=_mock_model(mocker, "TL;DR about users.py:42")):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        fmt = ReportFormatter(judge=judge)
        await fmt.run(state_with_finding)
    args, _kwargs = judge.evaluate.call_args
    eval_doc = args[0]
    criteria = args[1]
    # Findings ground truth surfaces in the doc
    assert "src/api/users.py" in eval_doc
    assert "sqli" in eval_doc
    # The TL;DR under review also surfaces
    assert "TL;DR about users.py:42" in eval_doc
    # Criteria list is non-empty and names look formatter-specific.
    assert criteria, "judge must receive criteria"
    names = {c.name for c in criteria}
    assert any("file" in n or "cve" in n or "severity" in n for n in names), (
        f"expected formatter-targeted criteria; got names: {names}"
    )
