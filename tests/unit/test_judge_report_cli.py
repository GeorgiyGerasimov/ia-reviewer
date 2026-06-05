"""CLI: `python -m src.evals.judge_report ...` evaluates one or more
published reports with the default rubric and prints a per-criterion
breakdown.

Contract:
- `judge_report --file <path>`: read the markdown, run LLMJudge against
  the default criteria (loaded from benchmarks/judge/cases.json), print
  the verdict, exit 0 on pass / 1 on fail / 2 on missing-file.
- `judge_report <thread_id>`: look up `reports/<thread_id>.md` next to
  the CWD's `reports/` dir. Same flow.
- Default rubric comes from `benchmarks/judge/cases.json::criteria`.
  An `--criteria <json-path>` flag overrides it.
- The `--recent N` flag reads N most-recent `.md` files from `reports/`
  (deterministic order by mtime descending) and judges each. Exit 0 if
  ALL pass; 1 if any fails.

Mocks LLMJudge at the boundary so no real network call ever happens
in tests. The CLI's own composition + arg-parsing + rendering is
what's under test.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.evals.judge_report import (
    _format_verdict,
    _load_default_criteria,
    _run_one_report,
    main,
)
from src.evals.llm_judge import CriterionResult, JudgeVerdict

# ── default criteria loader ───────────────────────────────────────────────────


def test_load_default_criteria_pulls_from_benchmarks_cases():
    """The default criteria are sourced from benchmarks/judge/cases.json
    so the production CLI and the calibration suite stay in sync."""
    criteria = _load_default_criteria()

    assert len(criteria) >= 3, f"expected ≥3 default criteria, got {len(criteria)}"
    names = {c.name for c in criteria}
    # Three rubric items we know live in the calibration set.
    assert "has_overall_severity" in names
    assert "mentions_all_roles" in names
    assert "no_fake_cve_format" in names


# ── single-report judging ─────────────────────────────────────────────────────


@pytest.fixture
def sample_report(tmp_path: Path) -> Path:
    """A plausible review report on disk, ready to be judged."""
    p = tmp_path / "abc-report.md"
    p.write_text(
        "## Security review\n\n"
        "**Overall severity:** minor\n\n"
        "### Dependencies — info\n\n"
        "no findings\n\n"
        "### Injection — minor\n\n"
        "- src/x.py:1 — sqli — [minor] Example\n\n"
        "### OWASP Top 10 — info\n\n"
        "no findings\n"
    )
    return p


async def test_run_one_report_calls_judge_with_loaded_content(mocker, sample_report):
    """`_run_one_report` reads the file, passes its content to the
    judge, returns whatever the judge returned."""
    mock_verdict = JudgeVerdict(
        passed=True,
        overall_score=8,
        per_criterion=[CriterionResult(name="has_overall_severity", met=True, evidence="ok")],
        rationale="looks fine",
    )
    mock_judge = mocker.AsyncMock()
    mock_judge.evaluate = mocker.AsyncMock(return_value=mock_verdict)

    result = await _run_one_report(sample_report, _load_default_criteria(), mock_judge)

    assert result == mock_verdict
    # The judge MUST have been called with the full file contents
    # (not the path, not a truncated version).
    sent_report = mock_judge.evaluate.await_args.args[0]
    assert "Overall severity:** minor" in sent_report
    assert "Injection — minor" in sent_report


# ── output formatting ────────────────────────────────────────────────────────


def test_format_verdict_renders_passed_with_overall_score():
    verdict = JudgeVerdict(
        passed=True,
        overall_score=9,
        per_criterion=[
            CriterionResult(name="has_overall_severity", met=True, evidence="line 3"),
            CriterionResult(name="mentions_all_roles", met=True, evidence="3 H3 sections"),
        ],
        rationale="report looks healthy",
    )
    out = _format_verdict("xyz-1.md", verdict)

    assert "xyz-1.md" in out
    assert "PASS" in out
    assert "9/10" in out or "9 / 10" in out or "score: 9" in out
    # Each criterion appears
    assert "has_overall_severity" in out
    assert "mentions_all_roles" in out


def test_format_verdict_renders_failed_with_failing_criteria_first():
    """When the judge says 'failed', the human reader's first question
    is 'which criterion?'. Failing criteria must be obvious in the
    output (e.g. with ✗ marker or grouped first)."""
    verdict = JudgeVerdict(
        passed=False,
        overall_score=4,
        per_criterion=[
            CriterionResult(name="has_overall_severity", met=True, evidence="ok"),
            CriterionResult(name="mentions_all_roles", met=False, evidence="OWASP missing"),
            CriterionResult(name="no_fake_cve_format", met=False, evidence="CVE-XXXX-XXXX"),
        ],
        rationale="missing role + placeholder CVE",
    )
    out = _format_verdict("broken.md", verdict)

    assert "FAIL" in out
    # Failing criteria carry their evidence string so the operator
    # can act on it.
    assert "OWASP missing" in out
    assert "CVE-XXXX-XXXX" in out


# ── CLI entrypoint ────────────────────────────────────────────────────────────


def test_cli_exits_0_when_report_passes(sample_report, mocker):
    """Pass → exit 0 so the CLI integrates with shell scripts / CI."""
    mock_verdict = JudgeVerdict(
        passed=True, overall_score=10, per_criterion=[], rationale="ok"
    )
    with patch("src.evals.judge_report._build_judge") as mk_judge:
        mk_judge.return_value.evaluate = mocker.AsyncMock(return_value=mock_verdict)
        exit_code = main(["--file", str(sample_report)])

    assert exit_code == 0


def test_cli_exits_1_when_report_fails(sample_report, mocker):
    mock_verdict = JudgeVerdict(
        passed=False, overall_score=2, per_criterion=[], rationale="nope"
    )
    with patch("src.evals.judge_report._build_judge") as mk_judge:
        mk_judge.return_value.evaluate = mocker.AsyncMock(return_value=mock_verdict)
        exit_code = main(["--file", str(sample_report)])

    assert exit_code == 1


def test_cli_exits_2_when_file_missing(tmp_path):
    """Missing file → exit 2 (clearly distinct from 'judge said fail')."""
    exit_code = main(["--file", str(tmp_path / "does-not-exist.md")])

    assert exit_code == 2


def test_cli_thread_id_resolves_to_reports_dir(tmp_path, monkeypatch, mocker):
    """A bare thread_id arg should resolve to `<reports_dir>/<id>.md`."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "tid-42.md").write_text("## Security review\n\nminimal\n")
    monkeypatch.chdir(tmp_path)

    mock_verdict = JudgeVerdict(
        passed=True, overall_score=8, per_criterion=[], rationale="ok"
    )
    with patch("src.evals.judge_report._build_judge") as mk_judge:
        mk_judge.return_value.evaluate = mocker.AsyncMock(return_value=mock_verdict)
        exit_code = main(["tid-42"])

    assert exit_code == 0
