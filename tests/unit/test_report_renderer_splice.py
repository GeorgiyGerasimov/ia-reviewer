"""ReportRenderer.splice_tldr — insert / replace a `### TL;DR` block.

This is the shared splice helper used by both the new `ReportFormatter`
agent (after `aggregate_results`) and `finalize_exploits` (after the
exploit loop re-renders the body). Keeping the splice pure here means
both callers produce byte-identical output for the same `(body, tldr)`
pair, and re-renders stay idempotent.

Contract:
  * `splice_tldr(body, tldr_text) -> str`
  * Insertion point: right after the `**Overall severity:**` line (one
    blank line of separation), BEFORE the next `### ` header. This lands
    the TL;DR between the overall-severity header and the deterministic
    `### Summary` table.
  * Empty `tldr_text` → return `body` unchanged.
  * If the body ALREADY contains a `### TL;DR` block, REPLACE it
    (idempotent — `finalize_exploits` re-renders the body and calls
    splice again).
  * Safety fallback: if `**Overall severity:**` is missing, splice
    after the `## Security review` title.
"""

from src.agents.report_renderer import ReportRenderer

_REPORT_WITH_SEVERITY = """## Security review

**Pull request:** https://github.com/o/r/pull/1

**Overall severity:** critical

### Summary

| Severity | Injection | Total |
|----------|-----------|-------|
| Critical | 1 | 1 |
"""


def test_splice_tldr_inserts_block_between_severity_and_summary():
    out = ReportRenderer().splice_tldr(_REPORT_WITH_SEVERITY, "One-paragraph summary.")
    pos_severity = out.find("**Overall severity:**")
    pos_tldr = out.find("### TL;DR")
    pos_summary = out.find("### Summary")
    assert -1 < pos_severity < pos_tldr < pos_summary, (
        f"TL;DR not between severity and summary; got order: "
        f"severity={pos_severity} tldr={pos_tldr} summary={pos_summary}\n"
        f"output:\n{out}"
    )
    assert "One-paragraph summary." in out


def test_splice_tldr_returns_body_unchanged_when_text_empty():
    assert ReportRenderer().splice_tldr(_REPORT_WITH_SEVERITY, "") == _REPORT_WITH_SEVERITY
    assert ReportRenderer().splice_tldr(_REPORT_WITH_SEVERITY, "   \n  ") == _REPORT_WITH_SEVERITY


def test_splice_tldr_is_idempotent_replaces_existing_block():
    """Re-splicing the same body must REPLACE the previous TL;DR rather than
    accumulate two blocks. Required for finalize_exploits which re-renders
    the body from scratch and calls splice again on each iteration."""
    once = ReportRenderer().splice_tldr(_REPORT_WITH_SEVERITY, "First text.")
    twice = ReportRenderer().splice_tldr(once, "Second text.")
    assert "Second text." in twice
    assert "First text." not in twice
    assert twice.count("### TL;DR") == 1


def test_splice_tldr_no_overall_severity_falls_back_to_after_title():
    """Some rejection-style or stub reports don't carry the overall-
    severity header. The splice should still work: insert after the
    `## Security review` title instead of silently dropping the TL;DR."""
    body = "## Security review\n\nbody body body\n"
    out = ReportRenderer().splice_tldr(body, "fallback summary")
    assert "### TL;DR" in out
    assert "fallback summary" in out
    pos_title = out.find("## Security review")
    pos_tldr = out.find("### TL;DR")
    assert pos_title < pos_tldr


def test_splice_tldr_strips_surrounding_whitespace_from_text():
    """LLM output often has leading/trailing newlines; we don't want
    those to leak into the report and create double blank lines."""
    out = ReportRenderer().splice_tldr(_REPORT_WITH_SEVERITY, "\n\nclean text.\n\n")
    # The TL;DR block has exactly one blank line around it, not three.
    assert "### TL;DR\n\nclean text.\n\n###" in out
