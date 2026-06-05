"""CLI: score one or more published review reports with `LLMJudge`.

This is Pattern 1 from [`docs/judge-in-production.md`](../../docs/judge-in-production.md)
— the "on-demand" entrypoint for grading a finished report against the
default rubric without touching the review pipeline.

Usage:

    python -m src.evals.judge_report <thread_id>
    python -m src.evals.judge_report --file path/to/report.md
    python -m src.evals.judge_report --recent 10
    python -m src.evals.judge_report <thread_id> --criteria my-rubric.json

Exit codes:
    0  — every report scored `passed=True`
    1  — at least one report scored `passed=False`
    2  — input error (file missing, malformed criteria, etc.)

The default rubric is loaded from `benchmarks/judge/cases.json::criteria`
so the production CLI and the calibration suite stay aligned. Override
with `--criteria <json-path>` whose top-level shape is either a bare
`[{name, description}, ...]` list, or `{criteria: [...]}` matching the
benchmark file layout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from src.evals.llm_judge import Criterion, JudgeVerdict, LLMJudge

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_RUBRIC_PATH = _REPO_ROOT / "benchmarks" / "judge" / "cases.json"
_DEFAULT_REPORTS_DIR = Path("reports")


# ── public API (used by tests + CLI) ──────────────────────────────────────────


def _load_default_criteria() -> list[Criterion]:
    """Load the default rubric from `benchmarks/judge/cases.json`.

    Keeps the production judge in sync with the calibration set. Any
    change to the rubric should round-trip through the benchmark
    (add a calibration case for the new criterion) so we can tell
    whether the new criterion is too strict / too loose.
    """
    return _load_criteria(_DEFAULT_RUBRIC_PATH)


def _load_criteria(path: Path) -> list[Criterion]:
    payload = json.loads(Path(path).read_text())
    raw = payload.get("criteria") if isinstance(payload, dict) else payload
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path}: expected list of criteria (or {{criteria: [...]}})")
    out: list[Criterion] = []
    for entry in raw:
        if not isinstance(entry, dict) or "name" not in entry or "description" not in entry:
            raise ValueError(
                f"{path}: each criterion must have 'name' + 'description'; got {entry!r}"
            )
        out.append(Criterion(name=entry["name"], description=entry["description"]))
    return out


async def _run_one_report(
    report_path: Path,
    criteria: list[Criterion],
    judge: LLMJudge,
) -> JudgeVerdict:
    """Load `report_path` and run the judge against it. Returns the
    `JudgeVerdict` unchanged so callers can render however they like.
    """
    content = report_path.read_text(encoding="utf-8")
    return await judge.evaluate(content, criteria)


def _format_verdict(report_label: str, verdict: JudgeVerdict) -> str:
    """Human-readable, terminal-friendly summary of a single verdict.

    Failing criteria are surfaced first so the operator's eye lands on
    the problem, not on a clean ✓ list. The rationale closes the
    block — it's the judge's one-sentence "why".
    """
    status = "PASS" if verdict.passed else "FAIL"
    lines = [
        f"━━━ {report_label} ━━━ {status} (score: {verdict.overall_score}/10)",
    ]
    failing = [c for c in verdict.per_criterion if not c.met]
    passing = [c for c in verdict.per_criterion if c.met]
    for c in failing:
        lines.append(f"  ✗  {c.name}: {c.evidence}")
    for c in passing:
        lines.append(f"  ✓  {c.name}: {c.evidence}")
    if verdict.rationale:
        lines.append(f"  → {verdict.rationale}")
    return "\n".join(lines)


# ── CLI plumbing ──────────────────────────────────────────────────────────────


def _build_judge() -> LLMJudge:
    """Indirection so tests can `patch('src.evals.judge_report._build_judge')`
    without monkey-patching the LLMJudge constructor."""
    return LLMJudge()


def _resolve_report_paths(args: argparse.Namespace) -> list[Path]:
    """Map CLI inputs to a concrete list of report paths."""
    if args.file:
        return [Path(args.file)]
    if args.recent:
        reports_dir = Path(args.reports_dir or _DEFAULT_REPORTS_DIR)
        if not reports_dir.is_dir():
            raise FileNotFoundError(f"reports dir not found: {reports_dir}")
        files = sorted(
            reports_dir.glob("*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        # Exclude sibling exploit artefacts (`<tid>.exploit.<fid>.md`) —
        # they're not full review reports the rubric expects.
        files = [f for f in files if ".exploit." not in f.name]
        return files[: args.recent]
    if args.thread_id:
        reports_dir = Path(args.reports_dir or _DEFAULT_REPORTS_DIR)
        return [reports_dir / f"{args.thread_id}.md"]
    raise ValueError("must provide <thread_id> or --file or --recent")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="judge_report",
        description="Run LLMJudge against finished ia-reviewer reports.",
    )
    parser.add_argument(
        "thread_id",
        nargs="?",
        help="Thread id → resolves to <reports_dir>/<id>.md",
    )
    parser.add_argument(
        "--file",
        help="Direct path to a report markdown file.",
    )
    parser.add_argument(
        "--recent",
        type=int,
        metavar="N",
        help="Score the N most-recent reports under <reports_dir>.",
    )
    parser.add_argument(
        "--reports-dir",
        default=None,
        help=f"Directory holding <thread_id>.md reports (default: {_DEFAULT_REPORTS_DIR}/).",
    )
    parser.add_argument(
        "--criteria",
        help=f"Custom rubric JSON file (default: {_DEFAULT_RUBRIC_PATH}).",
    )
    args = parser.parse_args(argv)

    # Load rubric.
    try:
        criteria = (
            _load_criteria(Path(args.criteria))
            if args.criteria
            else _load_default_criteria()
        )
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        print(f"ERROR loading criteria: {e}", file=sys.stderr)
        return 2

    # Resolve which reports to score.
    try:
        report_paths = _resolve_report_paths(args)
    except (ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if not report_paths:
        print("ERROR: no reports matched the criteria", file=sys.stderr)
        return 2

    # Existence check up-front — clearer error than "file not found"
    # during judge.evaluate.
    missing = [p for p in report_paths if not p.is_file()]
    if missing:
        for p in missing:
            print(f"ERROR: report not found: {p}", file=sys.stderr)
        return 2

    judge = _build_judge()
    all_passed = True
    for path in report_paths:
        verdict = asyncio.run(_run_one_report(path, criteria, judge))
        print(_format_verdict(path.name, verdict))
        print()
        if not verdict.passed:
            all_passed = False

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
