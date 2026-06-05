"""LLMJudge — LLM-as-judge evaluator for security review reports.

Contract:
- `LLMJudge.evaluate(report_markdown, criteria) -> JudgeVerdict` calls
  the LLM with the report + the criteria list, parses the structured
  JSON response, and returns a verdict.
- `JudgeVerdict(passed, overall_score, per_criterion, rationale)`:
  - `passed` — True iff every required criterion was met
  - `overall_score` — 0–10 holistic score (judge's free-form rating)
  - `per_criterion` — list of `CriterionResult(name, met, evidence)`
  - `rationale` — one-sentence summary
- Parser failure → fail-closed: `passed=False`, score 0, single
  per_criterion entry flagging the parser error. We don't quietly accept
  on parser failure (unlike the validator) because the judge is
  measuring quality — a broken judge response IS a quality signal.
"""

from unittest.mock import patch

from src.evals.llm_judge import (
    Criterion,
    JudgeVerdict,
    LLMJudge,
)

SAMPLE_REPORT = """## Security review

**Overall severity:** major

### Dependencies — minor
no findings

### Injection — major
- src/auth.py — sqli — [major] SQL via f-string in login flow

### OWASP Top 10 — info
no findings
"""

DEFAULT_CRITERIA = [
    Criterion("has_overall_severity", "Report has a '**Overall severity:**' header line."),
    Criterion("mentions_all_roles", "All three roles (Dependencies, Injection, OWASP Top 10) are mentioned."),
    Criterion("no_fake_cve_format", "Any CVE ids use the canonical CVE-YYYY-NNNN format."),
]


# ── happy path ────────────────────────────────────────────────────────────────


async def test_evaluate_returns_passed_when_judge_says_all_criteria_met(mocker):
    """Judge response: every criterion met → JudgeVerdict.passed=True."""
    judge_response = {
        "overall_score": 9,
        "passed": True,
        "per_criterion": [
            {"name": "has_overall_severity", "met": True, "evidence": "line 3"},
            {"name": "mentions_all_roles", "met": True, "evidence": "###'s"},
            {"name": "no_fake_cve_format", "met": True, "evidence": "no CVE ids present"},
        ],
        "rationale": "clean report",
    }
    mock_model = mocker.AsyncMock()
    mock_response = mocker.AsyncMock()
    mock_response.content = (
        '```json\n' + _json(judge_response) + '\n```'
    )
    mock_model.ainvoke = mocker.AsyncMock(return_value=mock_response)

    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        judge = LLMJudge()
        verdict = await judge.evaluate(SAMPLE_REPORT, DEFAULT_CRITERIA)

    assert isinstance(verdict, JudgeVerdict)
    assert verdict.passed is True
    assert verdict.overall_score == 9
    assert len(verdict.per_criterion) == 3
    assert all(c.met for c in verdict.per_criterion)


async def test_evaluate_returns_failed_when_any_criterion_unmet(mocker):
    """One criterion failing → JudgeVerdict.passed=False."""
    judge_response = {
        "overall_score": 5,
        "passed": False,
        "per_criterion": [
            {"name": "has_overall_severity", "met": True, "evidence": "line 3"},
            {"name": "mentions_all_roles", "met": False, "evidence": "OWASP missing"},
            {"name": "no_fake_cve_format", "met": True, "evidence": "n/a"},
        ],
        "rationale": "OWASP section missing entirely",
    }
    mock_model = mocker.AsyncMock()
    mock_response = mocker.AsyncMock()
    mock_response.content = '```json\n' + _json(judge_response) + '\n```'
    mock_model.ainvoke = mocker.AsyncMock(return_value=mock_response)

    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        judge = LLMJudge()
        verdict = await judge.evaluate(SAMPLE_REPORT, DEFAULT_CRITERIA)

    assert verdict.passed is False
    assert verdict.overall_score == 5
    failed = [c for c in verdict.per_criterion if not c.met]
    assert len(failed) == 1
    assert failed[0].name == "mentions_all_roles"
    assert "OWASP" in failed[0].evidence


async def test_evaluate_prompt_includes_report_and_criteria(mocker):
    """The prompt sent to the LLM must carry both the report text AND
    the criterion list so the judge has the full context."""
    mock_model = mocker.AsyncMock()
    mock_response = mocker.AsyncMock()
    mock_response.content = '{"passed": true, "overall_score": 10, "per_criterion": [], "rationale": "ok"}'
    mock_model.ainvoke = mocker.AsyncMock(return_value=mock_response)

    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        judge = LLMJudge()
        await judge.evaluate(SAMPLE_REPORT, DEFAULT_CRITERIA)

    sent_prompt = mock_model.ainvoke.await_args.args[0]
    # Report text appears verbatim somewhere in the prompt.
    assert "Overall severity:** major" in sent_prompt
    # Each criterion appears too.
    for c in DEFAULT_CRITERIA:
        assert c.name in sent_prompt
        assert c.description in sent_prompt


# ── parser failure paths ──────────────────────────────────────────────────────


async def test_evaluate_fails_closed_on_unparseable_response(mocker):
    """Garbage in → JudgeVerdict.passed=False. The judge measures
    quality; a broken judge response IS a quality signal, not a thing
    to silently accept."""
    mock_model = mocker.AsyncMock()
    mock_response = mocker.AsyncMock()
    mock_response.content = "not json at all, just prose"
    mock_model.ainvoke = mocker.AsyncMock(return_value=mock_response)

    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        judge = LLMJudge()
        verdict = await judge.evaluate(SAMPLE_REPORT, DEFAULT_CRITERIA)

    assert verdict.passed is False
    assert verdict.overall_score == 0
    # At least one criterion flag should mark the parser failure
    parse_flags = [c for c in verdict.per_criterion if "parse" in c.evidence.lower() or "parse" in c.name.lower()]
    assert parse_flags, (
        f"expected a per_criterion entry flagging the parse failure; "
        f"got {verdict.per_criterion!r}"
    )


async def test_evaluate_extracts_json_from_fenced_block(mocker):
    """LLM often wraps JSON in ```json fences; the parser must strip them."""
    inner = '{"passed": true, "overall_score": 8, "per_criterion": [], "rationale": "ok"}'
    mock_model = mocker.AsyncMock()
    mock_response = mocker.AsyncMock()
    mock_response.content = f"Here's my verdict:\n\n```json\n{inner}\n```\n\nThanks!"
    mock_model.ainvoke = mocker.AsyncMock(return_value=mock_response)

    with patch("src.models.factory.ModelFactory.get", return_value=mock_model):
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        judge = LLMJudge()
        verdict = await judge.evaluate(SAMPLE_REPORT, DEFAULT_CRITERIA)

    assert verdict.passed is True
    assert verdict.overall_score == 8


# ── model-selection precedence ────────────────────────────────────────────────


def test_judge_uses_settings_judge_model_when_constructor_arg_omitted(mocker):
    """When `settings.JUDGE_MODEL` is set and no `model_name=` is passed,
    the judge requests THAT model from `ModelFactory` — operator can
    pick a different (stronger) model than the reviewer without code
    changes."""
    mocker.patch("src.evals.llm_judge.settings.JUDGE_MODEL", "claude-opus-4-7")
    factory_get = mocker.patch(
        "src.models.factory.ModelFactory.get", return_value=mocker.AsyncMock()
    )
    from src.models.factory import ModelFactory
    ModelFactory._instances.clear()

    judge = LLMJudge()

    factory_get.assert_called_once_with("claude-opus-4-7")
    assert judge.model_name == "claude-opus-4-7"


def test_judge_constructor_arg_beats_settings_judge_model(mocker):
    """Explicit `model_name=` overrides the env-driven default — useful
    in tests and in callers that pin a specific model for an A/B."""
    mocker.patch("src.evals.llm_judge.settings.JUDGE_MODEL", "claude-opus-4-7")
    factory_get = mocker.patch(
        "src.models.factory.ModelFactory.get", return_value=mocker.AsyncMock()
    )
    from src.models.factory import ModelFactory
    ModelFactory._instances.clear()

    judge = LLMJudge(model_name="gpt-4o")

    factory_get.assert_called_once_with("gpt-4o")
    assert judge.model_name == "gpt-4o"


def test_judge_falls_back_to_builtin_default_when_both_unset(mocker):
    """No env var, no constructor arg → built-in default
    (`claude-sonnet-4-6`). Keeps the judge working out of the box."""
    mocker.patch("src.evals.llm_judge.settings.JUDGE_MODEL", "")
    factory_get = mocker.patch(
        "src.models.factory.ModelFactory.get", return_value=mocker.AsyncMock()
    )
    from src.models.factory import ModelFactory
    ModelFactory._instances.clear()

    judge = LLMJudge()

    factory_get.assert_called_once_with("claude-sonnet-4-6")
    assert judge.model_name == "claude-sonnet-4-6"


# ── helpers ───────────────────────────────────────────────────────────────────


def _json(obj):
    """Inline import-free JSON dumper for test fixtures."""
    import json
    return json.dumps(obj)
