"""`DependencyScanner` — script-first vulnerability discovery.

The scanner replaces what used to be `DependencyReviewer`'s per-file LLM
loop. Determinism is the whole point: given the same lockfile + same OSV
response, the findings list is byte-identical between runs. The LLM is
relegated to summary-only.

Contract:

  scanner = DependencyScanner(osv_client)
  result  = await scanner.scan(repo_files, snapshot_root)

  result.findings is a list[dict] shaped exactly like the reviewer's
                   existing `findings` schema:
                   {file, package, version, issue, severity, cve, fixed,
                    refs}.

  result.scanned_packages  = total Dep count fed to OSV.
  result.ecosystems        = sorted list of ecosystems we actually parsed.
  result.unsupported_files = list of file paths whose basename matches the
                             dependency whitelist but for which no parser
                             is wired (e.g. `go.sum`, `Cargo.lock`). Used
                             by the reviewer to disclose partial coverage.
  result.notes             = human-readable strings to include in the
                             eventual summary (parser errors, "OSV
                             unreachable", etc.).

Severity is derived from the OSV CVSS_V3 score, NOT from an LLM guess:
  CVSS >= 9.0 → critical
  CVSS >= 7.0 → major
  CVSS >= 4.0 → minor
  otherwise   → info
A missing/unparseable CVSS string falls back to `minor` (a real CVE is
worth surfacing even without a score).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.graph.state import RepoFile
from src.integrations.osv_client import Dep, Vuln
from src.scanners.dependency_scanner import DependencyScanner


def _write_lockfile_v3(root: Path, packages: dict) -> Path:
    """Write a minimal valid npm v3 `package-lock.json` under `root`."""
    p = root / "package-lock.json"
    p.write_text(json.dumps({"lockfileVersion": 3, "packages": packages}))
    return p


def _write_requirements(root: Path, body: str) -> Path:
    p = root / "requirements.txt"
    p.write_text(body)
    return p


@pytest.fixture
def osv():
    """A mock OSV client whose `query` is set per-test."""
    client = AsyncMock()
    client.query = AsyncMock(return_value={})
    return client


async def test_scan_npm_lockfile_emits_findings_from_osv(tmp_path, osv):
    """A vulnerable npm lockfile entry → one finding per OSV `Vuln` for
    that package. Fields come straight from the `Vuln` object (CVE id,
    fixed_versions, refs); no LLM in the loop."""
    _write_lockfile_v3(
        tmp_path,
        {
            "": {},
            "node_modules/lodash": {"version": "4.17.15"},
        },
    )
    dep = Dep(name="lodash", ecosystem="npm", version="4.17.15")
    osv.query.return_value = {
        dep: [
            Vuln(
                id="GHSA-p6mc-m468-83gw",
                aliases=["CVE-2020-8203"],
                summary="Prototype Pollution in lodash",
                severity="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:H/A:L",
                fixed_versions=["4.17.20"],
                references=["https://github.com/advisories/GHSA-p6mc-m468-83gw"],
            )
        ],
    }

    scanner = DependencyScanner(osv_client=osv)
    repo_files = [RepoFile(path="package-lock.json", content="", size=10)]
    result = await scanner.scan(repo_files, snapshot_root=tmp_path)

    assert len(result.findings) == 1
    f = result.findings[0]
    assert f["package"] == "lodash"
    assert f["version"] == "4.17.15"
    assert f["file"] == "package-lock.json"
    assert "CVE-2020-8203" in f["issue"] or "GHSA-p6mc" in f["issue"]
    assert f["cve"] == "CVE-2020-8203"
    assert "4.17.20" in f["fixed"]
    assert f["severity"] in {"major", "critical", "minor"}  # CVSS-derived
    # The OSV advisory link is preserved so the LLM/report can cite it.
    assert any("advisories" in url for url in f["refs"])


async def test_scan_clean_lockfile_yields_no_findings(tmp_path, osv):
    """OSV returns empty vulns → scan returns empty findings list,
    `scanned_packages` still reflects what was queried."""
    _write_lockfile_v3(
        tmp_path,
        {
            "": {},
            "node_modules/safe-pkg": {"version": "1.0.0"},
        },
    )
    safe = Dep(name="safe-pkg", ecosystem="npm", version="1.0.0")
    osv.query.return_value = {safe: []}

    scanner = DependencyScanner(osv_client=osv)
    result = await scanner.scan(
        [RepoFile(path="package-lock.json", content="", size=10)],
        snapshot_root=tmp_path,
    )
    assert result.findings == []
    assert result.scanned_packages == 1


async def test_scan_severity_is_derived_from_cvss_not_guessed(tmp_path, osv):
    """CVSS:3.1 base score → severity bucket. Three distinct vulns with
    different CVSS scores must land in different severity buckets."""
    _write_lockfile_v3(
        tmp_path,
        {
            "": {},
            "node_modules/critpkg": {"version": "1.0.0"},
            "node_modules/majpkg": {"version": "2.0.0"},
            "node_modules/minpkg": {"version": "3.0.0"},
        },
    )
    crit = Dep(name="critpkg", ecosystem="npm", version="1.0.0")
    maj = Dep(name="majpkg", ecosystem="npm", version="2.0.0")
    minor = Dep(name="minpkg", ecosystem="npm", version="3.0.0")

    osv.query.return_value = {
        # CVSS:3.1 vector with a numeric score embedded.
        crit: [Vuln(id="GHSA-c", severity="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H 9.8")],
        maj: [Vuln(id="GHSA-m", severity="CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N 7.5")],
        minor: [Vuln(id="GHSA-l", severity="CVSS:3.1/AV:N/AC:H/PR:H/UI:R/S:U/C:L/I:L/A:N 5.4")],
    }

    scanner = DependencyScanner(osv_client=osv)
    result = await scanner.scan(
        [RepoFile(path="package-lock.json", content="", size=10)],
        snapshot_root=tmp_path,
    )

    by_pkg = {f["package"]: f["severity"] for f in result.findings}
    assert by_pkg == {"critpkg": "critical", "majpkg": "major", "minpkg": "minor"}


async def test_scan_pip_requirements_uses_pypi_ecosystem(tmp_path, osv):
    """`requirements.txt` deps must be queried with `ecosystem='PyPI'`."""
    _write_requirements(tmp_path, "Django==4.2.7\n")
    dep = Dep(name="Django", ecosystem="PyPI", version="4.2.7")
    osv.query.return_value = {dep: []}

    scanner = DependencyScanner(osv_client=osv)
    result = await scanner.scan(
        [RepoFile(path="requirements.txt", content="", size=10)],
        snapshot_root=tmp_path,
    )

    # OSV was called with exactly one Dep, in the PyPI ecosystem.
    called_deps = osv.query.await_args.args[0]
    assert called_deps == [dep]
    assert "PyPI" in result.ecosystems
    assert result.scanned_packages == 1


async def test_scan_combines_multiple_manifests_in_one_osv_call(tmp_path, osv):
    """Multi-ecosystem snapshot — one combined batch hits OSV exactly
    ONCE. This is the whole point of batching: deterministic, cheap."""
    _write_lockfile_v3(
        tmp_path,
        {"": {}, "node_modules/lodash": {"version": "4.17.15"}},
    )
    _write_requirements(tmp_path, "Django==4.2.7\n")
    lodash = Dep(name="lodash", ecosystem="npm", version="4.17.15")
    django = Dep(name="Django", ecosystem="PyPI", version="4.2.7")
    osv.query.return_value = {lodash: [], django: []}

    scanner = DependencyScanner(osv_client=osv)
    result = await scanner.scan(
        [
            RepoFile(path="package-lock.json", content="", size=10),
            RepoFile(path="requirements.txt", content="", size=10),
        ],
        snapshot_root=tmp_path,
    )

    # Exactly one OSV.query call — not one per manifest file.
    assert osv.query.await_count == 1
    queried = osv.query.await_args.args[0]
    assert set(queried) == {lodash, django}
    assert sorted(result.ecosystems) == ["PyPI", "npm"]


async def test_scan_unsupported_lockfile_is_recorded_not_fatal(tmp_path, osv):
    """A `go.sum` in the file list (no parser wired yet) must NOT crash
    the scan — it lands in `result.unsupported_files` and the npm part
    of the same scan completes normally."""
    _write_lockfile_v3(
        tmp_path,
        {"": {}, "node_modules/safe": {"version": "1.0.0"}},
    )
    (tmp_path / "go.sum").write_text("example.com/foo v1.2.3 h1:deadbeef\n")
    osv.query.return_value = {Dep(name="safe", ecosystem="npm", version="1.0.0"): []}

    scanner = DependencyScanner(osv_client=osv)
    result = await scanner.scan(
        [
            RepoFile(path="package-lock.json", content="", size=10),
            RepoFile(path="go.sum", content="", size=20),
        ],
        snapshot_root=tmp_path,
    )

    assert "go.sum" in result.unsupported_files
    assert result.scanned_packages == 1  # the safe npm dep


async def test_scan_records_parser_errors_as_notes(tmp_path, osv):
    """A corrupt `package-lock.json` (invalid JSON) doesn't blow up the
    scan — it's caught and surfaced as a note. Other manifests still
    process."""
    (tmp_path / "package-lock.json").write_text("{ this is not json")
    _write_requirements(tmp_path, "Django==4.2.7\n")
    django = Dep(name="Django", ecosystem="PyPI", version="4.2.7")
    osv.query.return_value = {django: []}

    scanner = DependencyScanner(osv_client=osv)
    result = await scanner.scan(
        [
            RepoFile(path="package-lock.json", content="", size=10),
            RepoFile(path="requirements.txt", content="", size=10),
        ],
        snapshot_root=tmp_path,
    )

    assert any("package-lock.json" in note for note in result.notes)
    assert result.scanned_packages == 1  # the Django dep still scanned


async def test_scan_handles_osv_unavailable(tmp_path, osv):
    """When OSV raises (network down / 5xx), the scan reports the failure
    via `result.notes` and returns `findings=[]`. The graph keeps going —
    'OSV unavailable' is a partial result, not a fatal error."""
    _write_lockfile_v3(tmp_path, {"": {}, "node_modules/lodash": {"version": "4.17.15"}})
    osv.query.side_effect = RuntimeError("connection refused")

    scanner = DependencyScanner(osv_client=osv)
    result = await scanner.scan(
        [RepoFile(path="package-lock.json", content="", size=10)],
        snapshot_root=tmp_path,
    )

    assert result.findings == []
    assert any("OSV" in note for note in result.notes)


async def test_scan_is_deterministic_same_input_same_output(tmp_path, osv):
    """Two runs over identical input must produce byte-identical findings
    (same order, same content). This is the whole reason we moved off the
    LLM — `set(findings)` would have been enough but ordering matters for
    deterministic diffing of the rendered report."""
    _write_lockfile_v3(
        tmp_path,
        {
            "": {},
            "node_modules/lodash": {"version": "4.17.15"},
            "node_modules/express": {"version": "4.18.2"},
        },
    )
    lodash = Dep(name="lodash", ecosystem="npm", version="4.17.15")
    express = Dep(name="express", ecosystem="npm", version="4.18.2")
    vuln_payload = {
        lodash: [Vuln(id="GHSA-lodash", summary="bad", severity="CVSS:3.1/.../H 7.5")],
        express: [],
    }
    osv.query.return_value = vuln_payload

    scanner = DependencyScanner(osv_client=osv)
    repo_files = [RepoFile(path="package-lock.json", content="", size=10)]
    r1 = await scanner.scan(repo_files, snapshot_root=tmp_path)
    r2 = await scanner.scan(repo_files, snapshot_root=tmp_path)

    assert r1.findings == r2.findings
