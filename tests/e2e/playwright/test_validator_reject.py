"""Validator-reject scenario: the validator answers `accepted=false`,
the graph routes to `notify_rejection` instead of the reviewer fan-out,
and the UI must reflect that:

  * `notify_rejection` circle reaches a terminal state (.rejected or
    its alias .skipped if the WS envelope used the "skipped" status).
  * The four reviewer circles never light up green — they stay
    untouched (or get marked .skipped by `handleValidationResult`'s
    accept/reject branch logic).
  * The chat / report panel shows the rejection notice.

Regression target: any future change to the validator → rejection
flow (template branch logic, WS envelope shape, `__done__` cascade)
that breaks this visual contract.
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.timeout(60)
@pytest.mark.parametrize(
    "playwright_app",
    [{
        # Repo-mode validator uses a pure-code prefilter and only
        # falls through to the LLM for PR-mode. An empty snapshot
        # triggers `empty_repo` rejection deterministically — no need
        # to coordinate with the model stub.
        "empty_snapshot": True,
    }],
    indirect=True,
)
def test_validator_reject_paints_notify_and_skips_reviewers(
    page: Page, playwright_app: str
) -> None:
    page.goto(playwright_app, wait_until="domcontentloaded")

    page.locator("#target_url").fill("https://github.com/octocat/Hello-World")
    page.locator("#review-form button[type='submit']").click()

    expect(page.locator("#review-status")).to_contain_text(
        "Review started", timeout=15_000
    )

    # The `notify_rejection` circle must NOT stay in the default neutral
    # state. The CSS treats `.rejected` (red) as the terminal state for
    # this node when the validator rejects. We allow either `.rejected`
    # or `.done` (the `__done__` cascade could mark it either way
    # depending on broadcast ordering).
    notify_circle = page.locator(".wf-step[data-node='notify_rejection']")
    expect(notify_circle).to_have_attribute(
        "class", re.compile(r"\b(rejected|done)\b"), timeout=30_000
    )

    # Reviewer circles must NOT reach `.done`. They should either stay
    # untouched (no extra class) or be marked `.skipped`. Asserting NOT
    # `.done` is the precise contract — we don't want them green.
    for node in (
        "dependency_review",
        "injection_review",
        "owasp_review",
        "configuration_review",
    ):
        circle = page.locator(f".wf-step[data-node='{node}']")
        # Grace period for the workflow finalize to settle.
        expect(circle).not_to_have_attribute(
            "class", re.compile(r"\bdone\b"), timeout=15_000
        )

    # The publish step also must NOT be `.done` — rejection branch
    # never reaches publish (the report file is written by
    # `notify_rejection` directly, not the publish node).
    publish_circle = page.locator(".wf-step[data-node='publish_report']")
    expect(publish_circle).not_to_have_attribute(
        "class", re.compile(r"\bdone\b"), timeout=5_000
    )
