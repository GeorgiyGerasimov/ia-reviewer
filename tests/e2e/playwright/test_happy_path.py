"""Happy-path scenario: submit a repo URL, watch the workflow circles
light up green one by one, see the Active reviews panel appear, then
the final report render.

Stubs:
  * `ModelFactory.get` → fake model returning canned JSON.
  * `clone_repo` → fake temp dir with one `main.py`.
  * No Postgres / no Langfuse / no RAG.

This is the smallest Playwright scenario that exercises the WS pipe,
the workflow chart, per-file scan, and the report renderer. If any of
these regress, this fails.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect


def test_repo_review_happy_path_lights_workflow_and_renders_report(
    page: Page, playwright_app: str
) -> None:
    page.goto(playwright_app, wait_until="domcontentloaded")

    # 1. Submit a repo URL via the form.
    page.locator("#target_url").fill("https://github.com/octocat/Hello-World")
    page.locator("#review-form button[type='submit']").click()

    # 2. The form's status line should report a started thread.
    expect(page.locator("#review-status")).to_contain_text(
        "Review started", timeout=15_000
    )

    # 3. All four security-reviewer workflow circles should reach a
    #    terminal state. With the stub model returning `info` for each
    #    reviewer, every node should land in `.done` (green). The
    #    workflow finalizes when the `__done__` envelope arrives; we
    #    poll until the `Publish` circle is .done — that's the last
    #    one of the happy path before the (skipped, no findings)
    #    exploit branch.
    publish_circle = page.locator(".wf-step[data-node='publish_report']")
    expect(publish_circle).to_have_class(
        # Substring match — the class list also carries "wf-step",
        # spacing etc. `.done` is what we care about.
        # Playwright's `to_have_class` does an exact comparison on full
        # className, so we use a regex via `to_have_attribute("class", …)`.
        # Easier: use `.locator(..).evaluate(...)` to check classList.
        # Playwright shortcut:
        #   `expect(loc).to_have_attribute("class", lambda v: "done" in v)`
        # isn't supported; do an explicit JS eval instead.
        # For terseness here, we fall back to attribute match with a regex.
        # `expect(...).to_have_attribute("class", re.compile("done"))`
        # works in pytest-playwright 0.7+.
        # Using re:
        re.compile(r"\bdone\b"),
        timeout=30_000,
    )

    # 4. All four reviewer children should also be .done.
    for node in (
        "dependency_review",
        "injection_review",
        "owasp_review",
        "configuration_review",
    ):
        circle = page.locator(f".wf-step[data-node='{node}']")
        expect(circle).to_have_attribute(
            "class", re.compile(r"\bdone\b"), timeout=10_000
        )

    # 5. Final report panel renders Markdown — the agent-name heading
    #    should be present.
    report_body = page.locator("#report-body")
    expect(report_body).to_be_visible()
    expect(report_body).to_contain_text("Security review", timeout=15_000)

    # 6. Active reviews panel should drop back to hidden after
    #    completion (the registry unregisters in `finally:`).
    active_wrap = page.locator("#active-reviews")
    expect(active_wrap).to_have_attribute("hidden", "", timeout=15_000)
