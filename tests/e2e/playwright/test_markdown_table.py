"""Markdown-table scenario: the security report's `### Summary` block
is rendered as a real `<table>` (not a wall of pipe-separated text).

Was a real regression at one point — the tiny in-page Markdown→HTML
converter didn't know about tables and joined every row with spaces.
This test pins the contract.

We force the Injection reviewer to return a real finding so the
Summary table has non-zero counts (a 0-only table would render
identically with or without the table parser).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.timeout(60)
@pytest.mark.parametrize(
    "playwright_app",
    [{
        # One finding for `injection` → Summary table renders with
        # `Minor | 0 | 1 | 0 | 0 | 1` row. We need at least one
        # non-zero so `_render_summary_table` doesn't drop the block.
        "reviewer_findings": {
            "injection": (
                '[{"file": "src/foo.py", "line": 1, "category": "sqli", '
                '"issue": "playwright fixture finding", "severity": "minor"}]'
            ),
        },
    }],
    indirect=True,
)
def test_summary_block_renders_as_real_table(
    page: Page, playwright_app: str
) -> None:
    page.goto(playwright_app, wait_until="domcontentloaded")

    page.locator("#target_url").fill("https://github.com/octocat/Hello-World")
    page.locator("#review-form button[type='submit']").click()

    expect(page.locator("#review-status")).to_contain_text(
        "Review started", timeout=15_000
    )

    # Wait for the report to render. The final-report panel pulls
    # `/reports/<tid>.md` once `publish_report` fires its envelope.
    report_body = page.locator("#report-body")
    expect(report_body).to_contain_text("Summary", timeout=30_000)

    # The Summary block must be inside a `<table>`. Locator scoped to
    # report-body so we don't accidentally match a `<table>` somewhere
    # else on the page (there aren't any others today, but defensive).
    table = report_body.locator("table")
    expect(table).to_have_count(1, timeout=10_000)

    # Header row must contain the role labels.
    thead = table.locator("thead")
    expect(thead).to_contain_text("Severity")
    expect(thead).to_contain_text("Injection")
    expect(thead).to_contain_text("Configuration")
    expect(thead).to_contain_text("Total")

    # Body must have one row per severity (critical / major / minor / info)
    # plus a Total footer — 5 rows total.
    body_rows = table.locator("tbody tr")
    expect(body_rows).to_have_count(5)
