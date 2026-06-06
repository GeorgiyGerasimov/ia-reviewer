"""Static checks over `templates/index.html` — cheap guards against
classes of UI bugs that would otherwise need a browser to find.

What this catches that previous sessions caught only via user
screenshots:
  * `--radius-md` was used in CSS but never declared, so corners
    were square instead of rounded (silent fallback to 0). Caught
    by `test_all_css_vars_are_defined`.
  * `handleValidationResult` cascade forgot `configuration_review`,
    so its workflow circle stayed grey. Covered by the existing
    `test_ui_template_reviewers_sync.py`.
  * A future bug where someone adds a 5th `<div class="…">` to the
    `<aside>` without matching the radius/padding/background
    treatment of the other three. Caught by
    `test_aside_panels_share_visual_treatment`.

Pure-Python regex. No browser, no node, no Chrome MCP.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "index.html"


@pytest.fixture(scope="module")
def html_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


# ── 1. CSS custom properties — every `var(--x)` must be declared ──────


_DECL_RE = re.compile(r"--([a-zA-Z0-9-]+)\s*:")
_USE_RE = re.compile(r"var\(\s*--([a-zA-Z0-9-]+)")

# Some custom props are deliberately undefined and have an inline fallback
# (`var(--danger, #dc2626)`). Skip those — the fallback IS the value.
_USE_WITH_FALLBACK_RE = re.compile(r"var\(\s*--[a-zA-Z0-9-]+\s*,")


def test_all_css_vars_are_defined(html_text: str) -> None:
    """Every `var(--xxx)` without an inline fallback must have a matching
    `--xxx:` declaration somewhere in `:root` / `[data-theme="dark"]` /
    any other CSS rule. An undeclared variable silently resolves to
    `unset` (often a no-op or 0), which is what made the file-progress
    and active-reviews panels render with square corners — `--radius-md`
    was never declared.

    Fallback usage like `var(--danger, #dc2626)` is intentional and
    skipped: the fallback covers the missing case at zero cost.
    """
    declared = set(_DECL_RE.findall(html_text))
    used = set(_USE_RE.findall(html_text))

    # Filter out usages that DO have a fallback — those are safe.
    safe_with_fallback = {
        m.group(1)
        for m in re.finditer(r"var\(\s*--([a-zA-Z0-9-]+)\s*,", html_text)
    }
    used_without_fallback = used - safe_with_fallback

    missing = used_without_fallback - declared
    assert not missing, (
        f"CSS custom property used but never declared: {sorted(missing)}.\n"
        f"Browsers silently fall back to `unset` (often 0), which is what "
        f"made the panels render with square corners. "
        f"Either declare them in `:root` or add an inline fallback like "
        f"`var(--name, #fallback)`."
    )


# ── 2. HTML ids must be unique ────────────────────────────────────────


def test_html_ids_are_unique(html_text: str) -> None:
    """`document.getElementById(...)` returns the FIRST match and
    silently ignores duplicates — a copy-paste of a panel into a
    second context would break event handlers on the duplicate
    without any error in the console.

    Only inspects the HTML body (skipping the inline `<script>` block
    where id-like strings appear as JS string literals)."""
    # Carve out the inline script section so id="…" strings inside
    # JS literals don't trip the count.
    script_start = html_text.find("<script>")
    script_end = html_text.find("</script>", script_start)
    if script_start != -1 and script_end != -1:
        html_only = html_text[:script_start] + html_text[script_end + len("</script>"):]
    else:
        html_only = html_text

    ids = re.findall(r'\bid="([^"]+)"', html_only)
    duplicates = [item for item, count in Counter(ids).items() if count > 1]
    assert not duplicates, (
        f"Duplicate `id=` attributes in HTML: {duplicates}.\n"
        f"`getElementById` returns the first match silently — handlers on "
        f"the duplicate(s) never fire and the bug is invisible in the console."
    )


# ── 3. Inline script must parse (catch typos before browser sees them) ─


def test_inline_script_parses(html_text: str, tmp_path: Path) -> None:
    """Pull the inline `<script>…</script>` and run it through
    `node --check` (Node.js syntax check, no execution). Catches
    typos / missing brackets / stray-character bugs before a browser
    silently halts script execution and leaves half the page broken.

    Skipped when `node` isn't on PATH (CI image always has it via the
    base Python image's npm tooling; local dev sometimes doesn't).
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH; install Node.js to enable JS-syntax checks")

    # Match `<script>…</script>` blocks anywhere in the template.
    # `re.DOTALL` lets `.` span newlines. `re.IGNORECASE` covers
    # `<SCRIPT>` / `<Script>` variants — HTML tags are case-insensitive
    # by spec, and CodeQL flags case-sensitive HTML regexes (correctly,
    # even if our own template only uses lowercase today).
    scripts = re.findall(
        r"<script>(.*?)</script>", html_text, re.DOTALL | re.IGNORECASE,
    )
    assert scripts, "template has no inline <script> blocks — the JS may have moved"

    for i, body in enumerate(scripts):
        # Wrap in an IIFE so `await`-at-top-level (allowed in modules
        # only) doesn't trip `node --check`. The runtime semantics
        # are irrelevant — we want a parse-only pass.
        wrapped = "(async () => {\n" + body + "\n})();"
        js_file = tmp_path / f"inline_script_{i}.js"
        js_file.write_text(wrapped, encoding="utf-8")

        result = subprocess.run(
            [node, "--check", str(js_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"Inline <script> block #{i} has a syntax error:\n"
            f"{result.stderr}\n"
            f"This would make the browser silently halt execution of "
            f"the rest of the script — half the UI would stop working."
        )


# ── 4. Aside panels share visual treatment ────────────────────────────


def _find_css_block(css_text: str, selector: str) -> str | None:
    """Return the body of `selector { … }` (without braces), or None."""
    m = re.search(
        rf"(?:^|\n)\s*{re.escape(selector)}\s*\{{([^}}]*)\}}",
        css_text,
    )
    return m.group(1) if m else None


def test_aside_panels_share_visual_treatment(html_text: str) -> None:
    """The four left-sidebar panels (`.workflow`, `.file-progress`,
    `.active-reviews`, `.past-reviews`) must share the SAME values
    for `border-radius`, `background`, and `padding`. Diverging
    values cause the misaligned look the user spotted: some panels
    rounded, some square; some elevated, some flat.

    Read each rule's body and pull the property values out. We
    compare them as strings rather than parsing the CSS — that's
    enough to catch "this one says `12px`, that one says
    `var(--radius-md)`".
    """
    panels = (".workflow", ".file-progress", ".active-reviews", ".past-reviews")
    panel_props: dict[str, dict[str, str]] = {}

    for selector in panels:
        body = _find_css_block(html_text, selector)
        assert body is not None, f"CSS rule for {selector} not found in template"

        props: dict[str, str] = {}
        for prop in ("border-radius", "background", "padding"):
            m = re.search(rf"\b{re.escape(prop)}\s*:\s*([^;\n]+)", body)
            if m:
                props[prop] = m.group(1).strip()
        panel_props[selector] = props

    # Compare every panel against the first one as the reference.
    reference = panel_props[panels[0]]
    for panel in panels[1:]:
        for prop, expected in reference.items():
            actual = panel_props[panel].get(prop)
            assert actual == expected, (
                f"{panel} `{prop}` = {actual!r} but {panels[0]} = {expected!r}. "
                f"Either unify to match, or move the divergent rule to a "
                f"distinct class so the divergence is intentional and "
                f"locally documented."
            )
