"""Theme-toggle scenario: click the Dark/Light toggle in the header,
reload the page, assert the choice persisted via `localStorage`.

The theme is applied through `data-theme="dark"` on `<html>` — see
`templates/index.html` `applyTheme()`. The key bug class this catches:
a refactor that drops the localStorage write or moves the storage key
without migration. Operators would silently lose theme preference on
every reload, which is a common UX regression.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.timeout(45)
def test_dark_theme_choice_persists_across_reload(
    page: Page, playwright_app: str
) -> None:
    page.goto(playwright_app, wait_until="domcontentloaded")

    html = page.locator("html")
    toggle = page.locator(".theme-toggle")

    # Start state: either no attribute (light) or set explicitly.
    # We just need to know what we're toggling FROM to assert TO.
    initial = html.get_attribute("data-theme") or ""
    target = "light" if initial == "dark" else "dark"

    toggle.click()
    # The toggle should immediately flip the attribute.
    expect(html).to_have_attribute("data-theme", target, timeout=2_000)

    # Reload and confirm the choice survived via localStorage.
    page.reload(wait_until="domcontentloaded")
    expect(html).to_have_attribute("data-theme", target, timeout=5_000)

    # And the localStorage key is what we expect (`ia-reviewer-theme`).
    stored = page.evaluate("localStorage.getItem('ia-reviewer-theme')")
    assert stored == target, (
        f"localStorage `ia-reviewer-theme` = {stored!r}, expected {target!r}"
    )
