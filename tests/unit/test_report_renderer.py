"""`ReportRenderer` — pure-render module split out of CoordinatorAgent.

The renderer has ZERO I/O: no disk, no LangGraph, no GitHub. It takes
the data and returns a string. This lets the I/O nodes (`publish`,
`finalize_exploits`, `notify_rejection`) stay thin and lets the render
itself be unit-tested without spinning up a graph or a tempdir.

Contract (this test file is the contract):
  * `render_review(reviews, exploit_proposals, request, thread_id) -> str`
    – the main `<thread_id>.md` body.
  * `render_rejection(verdict) -> str`
    – the templated "review skipped" body keyed by `verdict.category`.
  * `render_exploit_sibling(ep, thread_id) -> str`
    – the standalone Markdown for a `save_mode="file"` artifact.
  * `exploit_artifact_filename(thread_id, finding_id) -> str`
    – stable filename helper.

Backwards-compat: existing tests still import `_render_exploit_section`
and `_render_exploit_sibling` from `src.agents.coordinator` — those
re-exports must keep working so we don't break the suite.
"""

import pytest

from src.agents.report_renderer import ReportRenderer
from src.graph.state import (
    AgentReview,
    ExploitProposal,
    RepoFile,
    ReviewRequest,
    ValidationVerdict,
)


@pytest.fixture
def renderer() -> ReportRenderer:
    return ReportRenderer()


def _review(role: str, severity: str, findings=None) -> AgentReview:
    return AgentReview(
        agent_name=f"{role.title()}Reviewer",
        role=role,
        findings=findings or [],
        summary=f"summary for {role}",
        severity=severity,
        passed=severity in ("info", "minor"),
    )


# ── render_review ──────────────────────────────────────────────────────


def test_render_review_smoke(renderer):
    """Two reviewers, no exploits — produces a valid Markdown report
    with the Security review heading + per-role sections."""
    md = renderer.render_review(
        reviews=[
            _review("injection", "critical", [{"file": "a.py", "issue": "SQLi", "severity": "critical"}]),
            _review("owasp", "minor"),
        ],
        exploit_proposals=[],
        request=ReviewRequest(pr_url="https://github.com/o/r/pull/1"),
        thread_id="tid-1",
    )
    assert "## Security review" in md
    assert "Injection" in md
    assert "OWASP Top 10" in md
    assert "**Overall severity:** critical" in md
    assert "Pull request:" in md


def test_render_review_dedups_by_role(renderer):
    """Across re-review cycles agent_reviews accumulates one entry per
    pass; the renderer keeps only the latest per role."""
    md = renderer.render_review(
        reviews=[
            _review("injection", "minor"),
            _review("injection", "critical", [{"issue": "RCE", "severity": "critical"}]),
        ],
        exploit_proposals=[],
        request=None,
    )
    # Only the latest pass (critical) should land
    assert "**Overall severity:** critical" in md
    assert md.count("### Injection") == 1


def test_render_review_no_reviews_returns_stub(renderer):
    md = renderer.render_review(reviews=[], exploit_proposals=[], request=None)
    assert "no reviewers ran" in md


def test_render_review_includes_exploit_section_when_proposals_present(renderer):
    md = renderer.render_review(
        reviews=[_review("injection", "critical")],
        exploit_proposals=[
            ExploitProposal(
                finding_id="abc",
                role="injection",
                severity="critical",
                proposal_text="exploit text",
                status="approved",
                artifact="curl ...",
                save_mode="embed",
            )
        ],
        request=None,
        thread_id="tid-X",
    )
    assert "## Exploit proposals" in md
    assert "Defensive use only" in md  # disclaimer surfaces


def test_render_review_omits_exploit_section_when_empty(renderer):
    md = renderer.render_review(
        reviews=[_review("injection", "info")],
        exploit_proposals=[],
        request=None,
    )
    assert "## Exploit proposals" not in md


# ── render_rejection ───────────────────────────────────────────────────


def test_render_rejection_uses_category_template(renderer):
    verdict = ValidationVerdict(
        accepted=False, category="docs_only", reason="all files are .md"
    )
    md = renderer.render_rejection(verdict)
    assert "documentation" in md.lower()
    assert "all files are .md" in md


def test_render_rejection_unknown_category_falls_back(renderer):
    verdict = ValidationVerdict(
        accepted=False, category="bizarre_category", reason="huh"
    )
    md = renderer.render_rejection(verdict)
    assert "huh" in md
    assert "## Security review" in md  # generic header still used


def test_render_rejection_none_verdict(renderer):
    """When the validator never ran (verdict=None), the renderer still
    produces SOMETHING readable — the I/O caller will probably skip
    publishing in that case, but the renderer must not crash."""
    md = renderer.render_rejection(None)
    assert isinstance(md, str)
    assert len(md) > 0


# ── render_exploit_sibling + filename helpers ──────────────────────────


def test_render_exploit_sibling_contains_disclaimer_and_artifact(renderer):
    ep = ExploitProposal(
        finding_id="zzz",
        role="injection",
        severity="critical",
        proposal_text="plan",
        status="approved",
        artifact="curl localhost:8000/x",
        save_mode="file",
    )
    md = renderer.render_exploit_sibling(ep, thread_id="tid-Y")
    assert "Defensive use only" in md
    assert "curl localhost:8000/x" in md
    assert "tid-Y.md" in md  # back-link to main report


def test_exploit_artifact_filename_pattern(renderer):
    assert renderer.exploit_artifact_filename("tid-1", "abc") == "tid-1.exploit.abc.md"


def test_exploit_artifact_filename_handles_empty_thread_id(renderer):
    """Missing thread_id falls back to a predictable string instead of crashing."""
    name = renderer.exploit_artifact_filename("", "abc")
    assert "abc" in name and ".md" in name


# ── back-compat: tests still import internals from coordinator ─────────


def test_legacy_render_exploit_section_import_still_works():
    """`tests/unit/test_render_exploit_save_mode.py` imports this name —
    keep the re-export so the existing suite doesn't break."""
    from src.agents.coordinator import _render_exploit_section

    assert callable(_render_exploit_section)


def test_legacy_render_exploit_sibling_import_still_works():
    """`tests/unit/test_exploit_disclaimer.py` imports this name."""
    from src.agents.coordinator import _render_exploit_sibling

    assert callable(_render_exploit_sibling)


# ── pure: no I/O dependencies ──────────────────────────────────────────


def test_renderer_has_no_disk_dependencies(renderer):
    """A `ReportRenderer` instantiated with no args must work — no
    reports_dir, no github, no graph. If this test starts requiring args
    the renderer is sneaking I/O back in."""
    md = renderer.render_review(
        reviews=[_review("owasp", "info")],
        exploit_proposals=[],
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            ref="main",
            repo_files=[RepoFile(path="a.py", content="", size=10)],
        ),
        thread_id="tid",
    )
    assert "Repository:" in md
