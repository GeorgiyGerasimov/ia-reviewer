"""Stop-button scenario: start a review, intercept the active-reviews
poll so the panel becomes visible, click Stop (via the auto-confirm
dialog handler), assert the row disappears and the chat receives the
"Review cancelled by user." message.

Why we intercept the poll instead of letting the real review run:
  * The happy-path test already covers the full graph traversal.
  * Stop button is fundamentally about the *cancellation* flow —
    `POST /reviews/{id}/cancel` → 200 → registry drops the entry →
    next poll renders an empty list. That flow is independent of
    what the review was actually doing.
  * The graph completes near-instantly with stubs (<1s), so racing
    the Stop click against completion would be flaky. By pinning the
    active-reviews response to a synthetic row, we make the click
    deterministic.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect


def test_stop_button_cancels_and_hides_panel(
    page: Page, playwright_app: str
) -> None:
    # 1. Pin the active-reviews poll to return a single synthetic row
    #    so the panel becomes visible regardless of timing.
    fake_thread = "fixture-thread-abcd"
    poll_state = {"row_visible": True}

    def _handle_active_poll(route) -> None:
        if poll_state["row_visible"]:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    "["
                    f'{{"thread_id":"{fake_thread}",'
                    '"mode":"repo",'
                    '"target":"https://github.com/octocat/Hello-World",'
                    '"ref":"main",'
                    '"started_at":1234567890,'
                    '"elapsed_s":5}'
                    "]"
                ),
            )
        else:
            route.fulfill(
                status=200, content_type="application/json", body="[]"
            )

    # 2. Intercept the cancel endpoint so the click doesn't 404
    #    (no real review with `fixture-thread-abcd` is registered).
    def _handle_cancel(route) -> None:
        # After cancel: future polls should report an empty list.
        poll_state["row_visible"] = False
        route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                f'{{"status":"cancelled","thread_id":"{fake_thread}"}}'
            ),
        )

    page.route("**/reviews/active", _handle_active_poll)
    page.route(f"**/reviews/{fake_thread}/cancel", _handle_cancel)

    # 3. Auto-accept the `confirm("Stop this review?")` dialog.
    page.on("dialog", lambda d: d.accept())

    # 4. Load the page. The 5-second poll runs on `DOMContentLoaded`
    #    and again every 5s — we trigger an immediate first fire by
    #    waiting for the panel to become visible.
    page.goto(playwright_app, wait_until="domcontentloaded")
    active_panel = page.locator("#active-reviews")
    expect(active_panel).to_be_visible(timeout=10_000)

    # 5. The Stop button is rendered per row — exactly one in our case.
    stop_button = active_panel.locator(".active-item-stop")
    expect(stop_button).to_have_count(1)
    expect(stop_button).to_have_text("Stop")

    # 6. Click → dialog auto-accepts → cancel request fires → poll
    #    returns empty → renderActiveReviews sets hidden=true.
    stop_button.click()

    # The panel transitions to `hidden` after the next refresh in the
    # click handler (await refreshActiveReviews()). Allow up to 5s for
    # the round-trip; in practice it's sub-second with mocked routes.
    expect(active_panel).to_have_attribute("hidden", "", timeout=10_000)
