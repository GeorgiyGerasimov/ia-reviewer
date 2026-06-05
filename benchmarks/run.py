"""Benchmark runner — loads cases from JSON, exercises the target
component, reports a success-rate.

Two suites:
  * validator   — RequestValidator against `benchmarks/validator/cases.json`
  * dependency  — DependencyScanner against `benchmarks/dependency/cases.json`,
                  with OSV responses mocked from the case's own payload

CLI usage:
    python -m benchmarks.run validator
    python -m benchmarks.run validator --mock-llm   # skip live LLM
    python -m benchmarks.run dependency
    python -m benchmarks.run all

Exit code is 0 when success_rate >= --min-success-rate (default 0.80),
1 otherwise. This is intended to be wired into CI as a regression
backstop ("validator must stay above 75% even with mocks").

The runner is import-safe and re-usable from pytest — see
`tests/integration/test_benchmark_runner.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import respx

from src.agents.validator import RequestValidator
from src.evals.llm_judge import Criterion, JudgeVerdict, LLMJudge
from src.graph.state import RepoFile, ReviewRequest, ReviewState
from src.integrations.osv_client import OSVClient
from src.scanners.dependency_scanner import DependencyScanner

# ── result container ──────────────────────────────────────────────────────────


@dataclass
class BenchmarkResult:
    """Outcome of a single suite run.

    `details` is a list of per-case dicts: ``{name, expected, actual, passed}``
    plus suite-specific extras (e.g. `expected_cves`, `actual_cves` for
    dependency). The `render()` method prints a human-readable summary;
    callers may also serialise `details` to JSON for CI dashboards.
    """

    suite: str
    details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.details)

    @property
    def passed(self) -> int:
        return sum(1 for d in self.details if d.get("passed"))

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def success_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def render(self) -> str:
        lines = [
            f"=== Benchmark: {self.suite} ===",
            f"PASS: {self.passed}/{self.total} ({self.success_rate:.0%})",
            "",
        ]
        for d in self.details:
            tick = "✓" if d.get("passed") else "✗"
            extra = ""
            if not d.get("passed"):
                extra = f"  expected={d.get('expected')!r}  actual={d.get('actual')!r}"
            lines.append(f"  {tick}  {d['name']}{extra}")
        return "\n".join(lines)


# ── validator suite ───────────────────────────────────────────────────────────


def load_validator_cases(path: Path | str) -> list[dict]:
    """Load the validator cases.json file. Returns the `cases` array."""
    payload = json.loads(Path(path).read_text())
    cases = payload.get("cases") or []
    if not cases:
        raise ValueError(f"no `cases` array in {path}")
    return cases


async def run_validator_benchmark(
    cases_path: Path | str,
    *,
    mock_llm: bool = False,
) -> BenchmarkResult:
    """Run every validator case and tally pass/fail.

    `mock_llm=True`: the LLM is replaced with an `AsyncMock` whose
    response always parses as "accept". The validator's pure-code
    prefilter still runs first, so rejects from the prefilter still
    fire — only the LLM-judge branch becomes a constant. Useful for
    measuring pure-code coverage in CI without a network dependency.

    `mock_llm=False`: the real LLM (via `ModelFactory`) is called. This
    is the diploma-grade measurement.
    """
    cases = load_validator_cases(cases_path)
    fixtures_dir = Path(cases_path).parent / "fixtures"
    result = BenchmarkResult(suite="validator")

    if mock_llm:
        # Constant "accept" response — JSON shape the validator's parser
        # expects for the LLM-judge branch.
        mock_response = AsyncMock()
        mock_response.content = (
            '{"verdict": "accept", "category": "accepted", '
            '"reason": "mock-llm always accepts"}'
        )
        mock_model = AsyncMock()
        mock_model.ainvoke = AsyncMock(return_value=mock_response)
        patch_target = patch(
            "src.models.factory.ModelFactory.get", return_value=mock_model
        )
    else:
        # `nullcontext`-style no-op — real model wired through factory.
        from contextlib import nullcontext

        patch_target = nullcontext()

    with patch_target:
        # Reset the factory's per-process cache so a fresh model gets
        # built that picks up the mock.
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()

        validator = RequestValidator()

        for case in cases:
            state = _validator_state_from_case(case, fixtures_dir)
            update = await validator.run(state)
            verdict = update.get("validation")

            actual = verdict.category if verdict else "(no verdict)"
            actual_accepted = bool(getattr(verdict, "accepted", False))
            expected = case["expected"]["category"]
            expected_accepted = case["expected"]["accepted"]

            passed = (
                actual == expected and actual_accepted == expected_accepted
            )
            result.details.append({
                "name": case["name"],
                "expected": expected,
                "expected_accepted": expected_accepted,
                "actual": actual,
                "actual_accepted": actual_accepted,
                "passed": passed,
            })

    return result


def _validator_state_from_case(case: dict, fixtures_dir: Path) -> ReviewState:
    mode = case.get("mode", "pr")
    if mode == "repo":
        count = case.get("repo_files_count", 0)
        repo_files = [
            RepoFile(path=f"src/file_{i}.py", content="", size=10)
            for i in range(count)
        ]
        request = ReviewRequest(
            mode="repo",
            repo_url="https://github.com/bench/sample",
            ref="main",
            repo_files=repo_files,
        )
    else:
        diff = (
            (fixtures_dir / case["diff_file"]).read_text()
            if "diff_file" in case
            else case.get("diff_inline", "")
        )
        request = ReviewRequest(
            mode="pr",
            pr_url="https://github.com/bench/sample/pull/1",
            diff=diff,
            files_changed=case.get("files_changed", []),
            author="bench@example.com",
        )
    return ReviewState(request=request)


# ── dependency suite ──────────────────────────────────────────────────────────


def load_dependency_cases(path: Path | str) -> list[dict]:
    payload = json.loads(Path(path).read_text())
    cases = payload.get("cases") or []
    if not cases:
        raise ValueError(f"no `cases` array in {path}")
    return cases


async def run_dependency_benchmark(cases_path: Path | str) -> BenchmarkResult:
    """Run every dependency case with OSV mocked from the case payload.

    For each case the runner:
      1. Materialises the manifest fixtures into a tempdir (= snapshot).
      2. Mocks OSV `POST /v1/querybatch` to return `case.osv.querybatch`.
      3. Mocks each `GET /v1/vulns/{id}` to return its detail from
         `case.osv.vuln_details`.
      4. Invokes `DependencyScanner.scan(...)`.
      5. Compares `ScanResult` against `case.expected`.

    Always 100%-reproducible — no network, no live OSV. The suite IS
    the regression test for the scanner's mapping layer.
    """
    cases = load_dependency_cases(cases_path)
    fixtures_dir = Path(cases_path).parent / "fixtures"
    result = BenchmarkResult(suite="dependency")

    for case in cases:
        passed, detail = await _run_one_dependency_case(case, fixtures_dir)
        result.details.append(detail)
        _ = passed  # captured inside detail
    return result


async def _run_one_dependency_case(case: dict, fixtures_dir: Path) -> tuple[bool, dict]:
    expected = case["expected"]
    name = case["name"]
    with tempfile.TemporaryDirectory(prefix="bench-dep-") as snap_str:
        snap = Path(snap_str)
        # Write each manifest into the snapshot at its repo-relative path.
        repo_files = []
        for m in case["manifests"]:
            target = snap / m["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((fixtures_dir / m["fixture"]).read_text())
            repo_files.append(
                RepoFile(path=m["path"], content="", size=target.stat().st_size)
            )

        # respx must wrap the httpx client creation, not the other way
        # around — respx swaps the AsyncClient transport at __enter__,
        # so any clients built before the context starts retain the real
        # transport and bypass mocks.
        with respx.mock:
            respx.post("https://api.osv.dev/v1/querybatch").mock(
                return_value=httpx.Response(200, json=case["osv"]["querybatch"])
            )
            for vid, detail in case["osv"]["vuln_details"].items():
                respx.get(f"https://api.osv.dev/v1/vulns/{vid}").mock(
                    return_value=httpx.Response(200, json=detail)
                )
            async with httpx.AsyncClient() as http_client:
                scanner = DependencyScanner(
                    osv_client=OSVClient(http_client=http_client)
                )
                scan = await scanner.scan(repo_files, snapshot_root=snap)

        findings = scan.findings
        actual_cves = sorted({f.get("cve") for f in findings if f.get("cve")})

        # Expectation checks
        problems: list[str] = []
        if "min_findings" in expected and len(findings) < expected["min_findings"]:
            problems.append(f"min_findings: expected ≥{expected['min_findings']}, got {len(findings)}")
        if "max_findings" in expected and len(findings) > expected["max_findings"]:
            problems.append(f"max_findings: expected ≤{expected['max_findings']}, got {len(findings)}")
        if expected.get("must_be_clean") and findings:
            problems.append(f"must_be_clean: got {len(findings)} findings")
        for cve in expected.get("must_include_cves", []):
            if cve not in actual_cves:
                problems.append(f"must_include_cves: missing {cve}")
        if "expected_severity" in expected:
            severities = {f.get("severity") for f in findings}
            if expected["expected_severity"] not in severities:
                problems.append(
                    f"expected_severity: {expected['expected_severity']} not in {severities}"
                )
        if "min_packages_scanned" in expected and scan.scanned_packages < expected["min_packages_scanned"]:
            problems.append(
                f"min_packages_scanned: expected ≥{expected['min_packages_scanned']}, "
                f"got {scan.scanned_packages}"
            )
        for unsup in expected.get("must_include_unsupported", []):
            if unsup not in scan.unsupported_files:
                problems.append(f"must_include_unsupported: missing {unsup}")
        if expected.get("must_have_notes") and not scan.notes:
            problems.append("must_have_notes: scan.notes is empty")

        passed = not problems
        detail = {
            "name": name,
            "expected": expected,
            "actual": {
                "findings_count": len(findings),
                "cves": actual_cves,
                "severities": sorted({f.get("severity") for f in findings if f.get("severity")}),
                "unsupported_files": list(scan.unsupported_files),
                "notes_count": len(scan.notes),
                "scanned_packages": scan.scanned_packages,
            },
            "passed": passed,
            "problems": problems,
        }
        return passed, detail


# ── judge suite ───────────────────────────────────────────────────────────────


def load_judge_cases(path: Path | str) -> dict:
    """Load the judge cases payload. Returns `{cases, criteria}`."""
    payload = json.loads(Path(path).read_text())
    cases = payload.get("cases") or []
    criteria = payload.get("criteria") or []
    if not cases or not criteria:
        raise ValueError(f"{path}: expected `cases` and `criteria` arrays")
    return {"cases": cases, "criteria": criteria}


async def run_judge_benchmark(
    cases_path: Path | str,
    *,
    mock_judge: bool = False,
) -> BenchmarkResult:
    """Calibrate the LLM judge against a labelled set of reports.

    Each case is one (report, expected verdict) pair. We ask the judge
    to evaluate the report against the shared rubric and compare its
    verdict against the calibration label. The success rate IS the
    judge's accuracy on its own calibration set — a regression alarm
    when the judge starts grading inconsistently.

    `mock_judge=True` replaces the LLM with a constant-accept stub
    (always passes, every criterion `met=true`). This (a) gives a
    deterministic floor in CI and (b) makes the calibration set
    visible: by construction, mock mode passes the "good" reports and
    misses every "bad" one. The miss count is the lower bound on the
    real judge's value-add.
    """
    payload = load_judge_cases(cases_path)
    cases = payload["cases"]
    criteria_dicts = payload["criteria"]
    criteria = [Criterion(c["name"], c["description"]) for c in criteria_dicts]
    fixtures_dir = Path(cases_path).parent / "fixtures"

    result = BenchmarkResult(suite="judge")

    if mock_judge:
        # Mocked judge always says "all good" — measures the calibration gap.
        from unittest.mock import AsyncMock, patch

        mock_model = AsyncMock()
        mock_response = AsyncMock()
        mock_response.content = json.dumps({
            "passed": True,
            "overall_score": 10,
            "per_criterion": [
                {"name": c.name, "met": True, "evidence": "mock judge: skipped"}
                for c in criteria
            ],
            "rationale": "mock judge",
        })
        mock_model.ainvoke = AsyncMock(return_value=mock_response)
        patch_target = patch("src.models.factory.ModelFactory.get", return_value=mock_model)
    else:
        from contextlib import nullcontext
        patch_target = nullcontext()

    with patch_target:
        from src.models.factory import ModelFactory
        ModelFactory._instances.clear()
        judge = LLMJudge()

        for case in cases:
            report = (fixtures_dir / case["fixture"]).read_text()
            verdict = await judge.evaluate(report, criteria)
            passed, detail = _grade_judge_case(case, verdict, criteria)
            result.details.append(detail)
            _ = passed

    return result


def _grade_judge_case(
    case: dict,
    verdict: JudgeVerdict,
    criteria: list[Criterion],
) -> tuple[bool, dict]:
    """Compare `verdict` against `case.expected`.

    `expected.passed` must match `verdict.passed`. If
    `expected.must_fail_criteria` is set, every criterion-name in that
    list must be `met=false` in the verdict. Anything else (extra
    failing criteria) is a softer signal — recorded in `notes` but
    doesn't flunk the case (the judge might catch issues we didn't
    label).
    """
    expected = case["expected"]
    expected_passed = expected.get("passed")
    must_fail = expected.get("must_fail_criteria", [])

    notes: list[str] = []

    problems: list[str] = []
    if expected_passed is not None and verdict.passed != expected_passed:
        problems.append(
            f"verdict.passed: expected={expected_passed}, got={verdict.passed}"
        )

    failed_names = {c.name for c in verdict.per_criterion if not c.met}
    for required in must_fail:
        if required not in failed_names:
            problems.append(
                f"must_fail_criteria: judge did not flag `{required}` as failing"
            )

    if expected_passed is False:
        unexpected_failures = failed_names - set(must_fail)
        if unexpected_failures:
            notes.append(
                f"judge also flagged unmodeled failures: {sorted(unexpected_failures)}"
            )

    passed = not problems
    detail = {
        "name": case["name"],
        "expected": expected,
        "actual": {
            "passed": verdict.passed,
            "overall_score": verdict.overall_score,
            "failed_criteria": sorted(failed_names),
            "rationale": verdict.rationale,
        },
        "passed": passed,
        "problems": problems,
        "notes": notes,
    }
    return passed, detail


# ── CLI ───────────────────────────────────────────────────────────────────────


_DEFAULT_VALIDATOR_PATH = Path(__file__).parent / "validator" / "cases.json"
_DEFAULT_DEPENDENCY_PATH = Path(__file__).parent / "dependency" / "cases.json"
_DEFAULT_JUDGE_PATH = Path(__file__).parent / "judge" / "cases.json"


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="benchmarks.run",
        description="Run ia-reviewer benchmark suites and report success rate.",
    )
    parser.add_argument(
        "suite",
        choices=("validator", "dependency", "judge", "all"),
        help="Which suite(s) to run.",
    )
    parser.add_argument(
        "--mock-llm",
        action="store_true",
        help="Replace the LLM with a constant-accept mock. For validator: "
        "skips network and measures pure-code prefilter coverage. For judge: "
        "skips network and measures the calibration gap (mock-judge accepts "
        "everything, so 'bad' calibration reports surface as misses).",
    )
    parser.add_argument(
        "--min-success-rate",
        type=float,
        default=0.80,
        help="Exit non-zero if any suite's success_rate < this (default 0.80).",
    )
    args = parser.parse_args(argv)

    suites_to_run: list[str] = (
        ["validator", "dependency", "judge"] if args.suite == "all" else [args.suite]
    )
    results: list[BenchmarkResult] = []
    for s in suites_to_run:
        if s == "validator":
            results.append(asyncio.run(
                run_validator_benchmark(_DEFAULT_VALIDATOR_PATH, mock_llm=args.mock_llm)
            ))
        elif s == "judge":
            results.append(asyncio.run(
                run_judge_benchmark(_DEFAULT_JUDGE_PATH, mock_judge=args.mock_llm)
            ))
        else:
            results.append(asyncio.run(
                run_dependency_benchmark(_DEFAULT_DEPENDENCY_PATH)
            ))

    for r in results:
        print(r.render())
        print()

    worst = min((r.success_rate for r in results), default=1.0)
    if worst < args.min_success_rate:
        print(
            f"FAIL: worst suite success_rate {worst:.0%} < "
            f"threshold {args.min_success_rate:.0%}"
        )
        return 1
    print(
        f"OK: all suites at or above {args.min_success_rate:.0%} threshold "
        f"(worst: {worst:.0%})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
