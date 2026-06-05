"""Dependency-vulnerability reviewer.

PR-mode keeps the legacy single-LLM-call behaviour against a unified
diff (no manifest cloning + parsing + OSV would buy us anything; the
diff is already small enough for the model).

Repo-mode is **script-first** (since the script-first refactor):

  1. `DependencyScanner` parses every supported manifest into `Dep`s.
  2. A single OSV.dev batch query produces deterministic vulnerability
     records (CVE id, fixed versions, refs, CVSS-derived severity).
  3. The LLM is invoked **at most once** on the resulting findings list
     to write a plain-English summary paragraph for the report. CVE ids
     and severity buckets come from OSV, not the model.

Why: the previous per-file LLM loop produced non-deterministic results
(model hallucinated CVE numbers, severity flipped between runs). With
the scanner driving the data, two consecutive scans over the same
lockfile produce byte-identical findings — only the prose summary
varies.
"""

from __future__ import annotations

from pathlib import Path

from src.agents.base_reviewer import (
    _SEVERITY_RANK,
    BLOCKING_SEVERITIES,
    ScriptedScannerReviewer,
)
from src.graph.state import AgentReview, ReviewState
from src.integrations.osv_client import OSVClient
from src.scanners.dependency_scanner import DependencyScanner, ScanResult
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DependencyReviewer(ScriptedScannerReviewer):
    role = "dependency"
    description = "Reviews dependency manifests and lockfiles for vulnerable, outdated, or suspicious packages."
    # Repo-mode whitelist — manifests and lockfiles across the common ecosystems.
    # Matched against basenames via fnmatch, so e.g. "package.json" hits files
    # at any depth in the tree. The scanner has parsers for a subset of these;
    # the rest land in `ScanResult.unsupported_files` and are surfaced in the
    # final summary so reviewers know coverage is partial.
    PATH_PATTERNS = (
        # JS / TS
        "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
        # Python
        "requirements*.txt", "Pipfile", "Pipfile.lock", "poetry.lock", "pyproject.toml", "setup.py", "setup.cfg",
        # Go
        "go.mod", "go.sum",
        # Rust
        "Cargo.toml", "Cargo.lock",
        # Ruby
        "Gemfile", "Gemfile.lock",
        # PHP
        "composer.json", "composer.lock",
        # Java / Kotlin
        "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle*", "gradle.lockfile",
        # .NET
        "*.csproj", "*.fsproj", "*.vbproj", "packages.config",
    )

    # ── PR-mode prompt (unchanged) ────────────────────────────────────
    prompt_template = """You are a dependency-security reviewer.

Examine the diff for issues in package manifests and lockfiles (requirements.txt,
pyproject.toml, package.json, package-lock.json, yarn.lock, go.mod/go.sum,
Cargo.toml/Cargo.lock, Gemfile.lock, etc.).

Look for:
- Added or upgraded packages with known CVEs (call out the CVE id if confident)
- Pinned versions that are end-of-life or unmaintained
- Suspicious package names (typosquats, namespace confusion)
- Lockfile drift — entries added without corresponding manifest changes
- Transitive risk: a top-level addition that pulls a known-vulnerable transitive

Respond with one fenced JSON block matching this schema:

```json
{{
  "findings": [
    {{"file": "requirements.txt", "package": "...", "version": "...", "issue": "...", "severity": "critical|major|minor|info"}}
  ],
  "summary": "one-paragraph plain-English summary",
  "severity": "highest severity across findings; info if no findings"
}}
```

If the diff contains no dependency-manifest or lockfile changes, return
`findings: []`, `summary: "no dependency changes"`, `severity: "info"`.

Context:
{context}
"""

    # ── Repo-mode summary prompt (one LLM call after the scanner) ─────
    _SUMMARY_PROMPT = """You are summarising a vulnerability scan for a security review report.

The findings below come from a deterministic scan of dependency manifests
in a Git repository against OSV.dev (the canonical open-source vulnerability
database). DO NOT invent CVE numbers, packages, or severities — only describe
what's in the list.

Findings:
{findings_block}

{disclosures}

Write 2-4 short paragraphs of plain English for a security engineer. Cover:
  1. How many vulnerabilities, in how many packages, and the highest severity.
  2. The most impactful issues (focus on critical / major), naming the package,
     the CVE id, and the fixed version when known.
  3. Any partial-coverage caveats from the disclosures block (unsupported
     manifests, parser errors, OSV outage).

If `Findings` is empty, simply state that no known vulnerabilities were
found across the parsed manifests, plus the disclosure caveats."""

    async def _run_repo(self, state: ReviewState) -> dict:
        request = state.request
        snapshot_root = Path(request.snapshot_dir) if request.snapshot_dir else None
        if snapshot_root is None or not snapshot_root.exists():
            logger.warning(
                "dependency: snapshot_dir missing in repo-mode; skipping (got %r)",
                request.snapshot_dir,
            )
            return {}

        matched = [f for f in request.repo_files if self._matches_path(f.path)]
        if not matched:
            review = AgentReview(
                agent_name=self.__class__.__name__,
                role=self.role,
                findings=[],
                summary="No dependency manifests found in this repository snapshot.",
                severity="info",
                passed=True,
            )
            return {"agent_reviews": [review]}

        scanner = DependencyScanner(osv_client=OSVClient())
        result = await scanner.scan(matched, snapshot_root=snapshot_root)

        severity = _overall_severity(result.findings)
        summary = await self._summarise(result, severity)

        review = AgentReview(
            agent_name=self.__class__.__name__,
            role=self.role,
            findings=result.findings,
            summary=summary,
            severity=severity,
            passed=severity not in BLOCKING_SEVERITIES,
        )
        return {"agent_reviews": [review]}

    async def _summarise(self, result: ScanResult, severity: str) -> str:
        """Single LLM call (skipped when there are no findings AND no
        disclosures worth narrating)."""
        disclosures = _format_disclosures(result)
        if not result.findings:
            # No vulnerabilities + no disclosures → boring case, no LLM
            # round-trip. We compose the deterministic summary inline.
            if not disclosures.strip():
                return (
                    f"No known vulnerabilities across {result.scanned_packages} "
                    f"scanned packages "
                    f"({', '.join(result.ecosystems) or 'no recognised ecosystems'})."
                )
            return (
                f"No known vulnerabilities across {result.scanned_packages} "
                f"scanned packages. {disclosures}"
            )

        prompt = self._SUMMARY_PROMPT.format(
            findings_block=_format_findings(result.findings),
            disclosures=disclosures or "No partial-coverage caveats.",
        )
        response = await self.model.ainvoke(prompt)
        body = getattr(response, "content", "") or ""
        body = body.strip()
        if not body:
            # Degenerate LLM response — fall back to a deterministic line.
            body = (
                f"{len(result.findings)} vulnerability finding(s) across "
                f"{result.scanned_packages} packages; "
                f"highest severity {severity}."
            )
        return body


# ── helpers ────────────────────────────────────────────────────────────


def _overall_severity(findings: list[dict]) -> str:
    """Highest severity across all findings. `info` when the list is empty."""
    if not findings:
        return "info"
    max_rank = max(
        _SEVERITY_RANK.get(f.get("severity", "info"), 0) for f in findings
    )
    for label, rank in _SEVERITY_RANK.items():
        if rank == max_rank:
            return label
    return "info"


def _format_findings(findings: list[dict]) -> str:
    """Render the findings list for the LLM prompt as compact bullet lines.

    Format kept stable so the model's prose stays grounded — every line
    has package, version, CVE/advisory id, severity, and fixed-version
    info up front."""
    lines = []
    for f in findings:
        cve = f.get("cve") or f.get("advisory_id", "?")
        fixed = f.get("fixed") or []
        fixed_str = f", fix: {', '.join(fixed)}" if fixed else ""
        lines.append(
            f"- {f.get('package')} {f.get('version')} [{f.get('severity')}] "
            f"{cve}{fixed_str} — {f.get('issue', '')}"
        )
    return "\n".join(lines)


def _format_disclosures(result: ScanResult) -> str:
    """Build the partial-coverage block the LLM (or inline summary) sees."""
    parts: list[str] = []
    if result.unsupported_files:
        parts.append(
            "Recognised but not scanned (no parser yet): "
            + ", ".join(result.unsupported_files)
        )
    for note in result.notes:
        parts.append(note)
    return "\n".join(parts)
