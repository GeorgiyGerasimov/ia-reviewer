"""Script-first dependency-vulnerability scanner.

Replaces the per-file LLM loop that used to live in `DependencyReviewer`.
The LLM was hallucinating CVEs and producing different results on each
run. The new pipeline is **deterministic**:

  1. Walk the `repo_files` list. For each file whose basename is in
     `_PARSERS`, invoke the wired parser on the on-disk path inside
     `snapshot_root`. Output: list[Dep].
  2. Concatenate all Deps across all manifests, dedupe by (name,
     ecosystem, version), feed the combined batch to OSV.dev in a single
     `OSVClient.query` call.
  3. Map every returned `Vuln` to a finding dict with the same schema the
     reviewer used before — so the rendered Markdown report stays
     compatible.

The LLM is invoked exactly **once** by the caller (DependencyReviewer)
on the resulting findings list to write a plain-English summary
paragraph. CVE ids, fixed versions, references, and severity buckets
come from OSV, not from the model.

`ScanResult` carries the data needed for honest partial-coverage
disclosure:
  - `unsupported_files`: matched the dependency whitelist but no parser
    is wired (`go.sum`, `Cargo.lock`, `pom.xml`, …). The reviewer
    surfaces this in the report so reviewers know coverage is partial.
  - `notes`: human-readable strings about parser errors, OSV unreachable,
    etc.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from src.graph.state import RepoFile
from src.integrations.manifests.npm import parse_package_lock
from src.integrations.manifests.pip import parse_requirements_txt
from src.integrations.osv_client import Dep, OSVClient, Vuln
from src.utils.logger import get_logger

logger = get_logger(__name__)


# Langfuse v4 `@observe` decorator. When Langfuse hasn't been initialised
# (no keys / package missing), the decorator silently no-ops — the scanner
# stays usable without observability wired. When the lifespan has run
# `get_langfuse_callback()`, calls to `scan` and `query` create child
# spans attached to the surrounding graph trace via OTel context.
try:
    from langfuse import observe as _observe
except ImportError:  # pragma: no cover - exercised only when the package is missing
    def _observe(*_args, **_kwargs):
        def decorator(fn):
            return fn
        return decorator


# Map basename → (parser callable, source ecosystem string for surfacing).
# Parsers all share the signature `(Path) -> list[Dep]`.
_PARSERS: dict[str, Callable[[Path], list[Dep]]] = {
    "package-lock.json": parse_package_lock,
    "requirements.txt": parse_requirements_txt,
}

# Basenames that are *recognised* as dependency manifests but for which
# no parser is wired yet — recorded in `result.unsupported_files` so the
# reviewer can disclose partial coverage instead of silently dropping
# them.
_UNSUPPORTED_BASENAMES: frozenset[str] = frozenset({
    "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "Pipfile", "Pipfile.lock", "poetry.lock", "pyproject.toml",
    "setup.py", "setup.cfg",
    "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock",
    "Gemfile", "Gemfile.lock",
    "composer.json", "composer.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.lockfile",
    "packages.config",
})


# CVSS score → severity bucket. Matches the buckets used by the rest of
# the codebase (`_SEVERITY_RANK` in base_reviewer.py).
_CVSS_PREFIX_RE = re.compile(r"^CVSS:[0-9]+(?:\.[0-9]+)?/?")


def _severity_from_cvss(score_string: str | None) -> str:
    """Extract a numeric base score from an OSV severity string and map
    it to a bucket. Falls back to `minor` when no parseable number is
    present — a real CVE is still worth surfacing even without a score.

    OSV `severity[].score` is usually a CVSS vector like
    `CVSS:3.1/AV:N/AC:L/...`. The version prefix (`3.1`) is NOT the base
    score and must be stripped before scanning for a numeric. Some
    advisories append the actual score (`... 9.8`); when missing, we
    default to `minor`.
    """
    if not score_string:
        return "minor"
    body = _CVSS_PREFIX_RE.sub("", score_string).strip()
    # Find a standalone numeric token that isn't glued to letters / slashes
    # (so `AV:N`, `C:H`, etc. don't get picked up).
    match = re.search(r"(?<![A-Za-z/:.\d])([0-9]+(?:\.[0-9])?)(?![A-Za-z/:.\d])", body)
    if not match:
        return "minor"
    try:
        score = float(match.group(1))
    except ValueError:
        return "minor"
    if not 0.0 <= score <= 10.0:
        return "minor"
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "major"
    if score >= 4.0:
        return "minor"
    return "info"


@dataclass
class ScanResult:
    findings: list[dict]
    scanned_packages: int = 0
    ecosystems: list[str] = field(default_factory=list)
    unsupported_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class DependencyScanner:
    """Stateless orchestrator — `osv_client` is the only collaborator.

    `scan` is called from `DependencyReviewer._run_repo`. Returns
    `ScanResult`; the reviewer is responsible for any LLM summarisation
    on top of `result.findings`.
    """

    osv_client: OSVClient

    @_observe(name="dependency_scan", as_type="tool")
    async def scan(
        self,
        repo_files: list[RepoFile],
        *,
        snapshot_root: Path,
    ) -> ScanResult:
        deps: list[Dep] = []
        unsupported: list[str] = []
        notes: list[str] = []
        # Per-Dep mapping back to the manifest path it came from, so a
        # finding can cite the right file in the report. A Dep may appear
        # in multiple manifests (rare); we keep the first source we saw.
        dep_to_file: dict[Dep, str] = {}

        for repo_file in repo_files:
            basename = repo_file.path.rsplit("/", 1)[-1]
            parser = _PARSERS.get(basename)
            if parser is None:
                if basename in _UNSUPPORTED_BASENAMES:
                    unsupported.append(repo_file.path)
                continue
            try:
                parsed_deps = parser(snapshot_root / repo_file.path)
            except (OSError, ValueError, KeyError) as exc:
                notes.append(
                    f"{repo_file.path}: parser error — {type(exc).__name__}: {exc}"
                )
                continue
            for dep in parsed_deps:
                if dep not in dep_to_file:
                    dep_to_file[dep] = repo_file.path
                    deps.append(dep)

        ecosystems = sorted({dep.ecosystem for dep in deps})

        if not deps:
            return ScanResult(
                findings=[],
                scanned_packages=0,
                ecosystems=ecosystems,
                unsupported_files=unsupported,
                notes=notes,
            )

        # Single OSV round-trip for the whole batch — even across
        # ecosystems. OSV's batch API accepts mixed-ecosystem queries.
        try:
            vuln_map = await self.osv_client.query(deps)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dependency_scanner: OSV unreachable: %s", exc)
            notes.append(f"OSV unavailable — {type(exc).__name__}: {exc}")
            return ScanResult(
                findings=[],
                scanned_packages=len(deps),
                ecosystems=ecosystems,
                unsupported_files=unsupported,
                notes=notes,
            )

        findings = _render_findings(deps, vuln_map, dep_to_file)
        return ScanResult(
            findings=findings,
            scanned_packages=len(deps),
            ecosystems=ecosystems,
            unsupported_files=unsupported,
            notes=notes,
        )


def _render_findings(
    deps: list[Dep],
    vuln_map: dict[Dep, list[Vuln]],
    dep_to_file: dict[Dep, str],
) -> list[dict]:
    """Convert the OSV mapping into the reviewer's finding-dict schema.

    Stable order: iterate `deps` in input order (which itself reflects
    the manifest-file order). For each Dep, iterate its vulns in OSV's
    response order. Both orderings are deterministic, so the resulting
    `findings` list is byte-identical across runs given the same inputs.
    """
    out: list[dict] = []
    for dep in deps:
        for vuln in vuln_map.get(dep, []):
            cve = _first_cve_alias(vuln)
            issue = vuln.summary or f"Vulnerable dependency ({vuln.id})"
            if cve and cve not in issue:
                issue = f"{cve}: {issue}"
            out.append(
                {
                    "file": dep_to_file.get(dep, ""),
                    "package": dep.name,
                    "version": dep.version,
                    "ecosystem": dep.ecosystem,
                    "advisory_id": vuln.id,
                    "cve": cve,
                    "issue": issue,
                    "severity": _severity_from_cvss(vuln.severity),
                    "fixed": vuln.fixed_versions,
                    "refs": vuln.references,
                }
            )
    return out


def _first_cve_alias(vuln: Vuln) -> str:
    for alias in vuln.aliases or []:
        if alias.upper().startswith("CVE-"):
            return alias
    return ""
