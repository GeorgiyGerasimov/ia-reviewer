"""`templates/index.html` references reviewer node names in five places
that all must stay in sync with `_SECURITY_NODES` in
`src/graph/coordinator.py`. When a new reviewer is added the
Python side gets covered by topology tests, but the HTML/JS side
has historically been edited by eye — and at least one place got
forgotten (the cascade in `handleValidationResult` was missed when
ConfigurationReviewer was added, so its workflow circle never went
blue + spinner during a live run).

This test reads the rendered template as plain text and asserts
that EVERY `_SECURITY_NODES` entry appears in:

  1. The workflow-chart HTML (`data-node="<n>_review"`).
  2. The JS `SECURITY_NODES` array.
  3. The JS `NEXT_AFTER` map (as a key with `review_decision` target).
  4. The JS `ACCEPT_PATH_NODES` array.
  5. The cascade list inside `handleValidationResult`.

If any of these forget the new reviewer, the UI silently looks
broken (gray circle that never spins, or stays blue forever after
the run finishes). A regex-level grep is fine — we're guarding
against accidental omission, not against full JS semantic drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.graph.coordinator import _SECURITY_NODES

# `templates/index.html` lives at the repo root.
TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "index.html"


@pytest.fixture(scope="module")
def html_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


# ── 1. Workflow chart HTML — one `<div data-node="...">` per reviewer ──


def test_workflow_chart_lists_every_reviewer(html_text: str) -> None:
    """Each reviewer must have a workflow-chart slot so the dot/label
    can be addressed by `STEPS.get(node)` and flipped to active/done."""
    for node in _SECURITY_NODES:
        # The chart renders security children as `<div ... data-node="X">`.
        # Match the attribute exactly (with quotes) so we don't false-
        # positive on a substring of some longer node name.
        assert f'data-node="{node}"' in html_text, (
            f"workflow chart missing data-node='{node}'; "
            f"add it under the 'Security reviewers' branch in templates/index.html"
        )


# ── 2-5. JS constants ──────────────────────────────────────────────────


def _extract_js_array(name: str, html: str) -> list[str]:
    """Return string contents of `const NAME = ["a", "b", ...]`.

    Permissive enough to span across newlines (the file uses both
    styles). Returns `[]` when the const isn't found — caller turns
    that into a clear assertion failure."""
    pattern = rf"const\s+{re.escape(name)}\s*=\s*\[([^\]]*)\]"
    m = re.search(pattern, html)
    if not m:
        return []
    body = m.group(1)
    # Pull every quoted token (single, double, or backtick).
    return re.findall(r"""["'`]([^"'`]+)["'`]""", body)


def test_js_security_nodes_array_matches(html_text: str) -> None:
    """The JS `SECURITY_NODES` must contain exactly the same set as
    `_SECURITY_NODES` in coordinator.py (order doesn't matter — UI
    iterates as a set for the parallel-fanout completion check)."""
    js_nodes = _extract_js_array("SECURITY_NODES", html_text)
    assert js_nodes, "JS const SECURITY_NODES not found in template"
    assert set(js_nodes) == set(_SECURITY_NODES), (
        f"JS SECURITY_NODES = {set(js_nodes)} but coordinator._SECURITY_NODES "
        f"= {set(_SECURITY_NODES)}; templates/index.html must mirror the graph"
    )


def test_js_next_after_has_every_reviewer_routing_to_review_decision(html_text: str) -> None:
    """The JS `NEXT_AFTER` map drives "after node X fired, mark
    successors active" cascading. Every reviewer must be a key
    routing to `review_decision` (the fan-in node). Without this,
    the spinner never propagates correctly when the user opens an
    in-flight thread mid-run."""
    for node in _SECURITY_NODES:
        # Loose match: `<node>: ["review_decision"]` in the map. Allow
        # any whitespace around `:` and any quoting style around the
        # value. The map is short enough that a regex is readable.
        pattern = rf"{re.escape(node)}\s*:\s*\[[^\]]*review_decision[^\]]*\]"
        assert re.search(pattern, html_text), (
            f"NEXT_AFTER in templates/index.html is missing "
            f"`{node}: [\"review_decision\"]`"
        )


def test_js_accept_path_nodes_includes_every_reviewer(html_text: str) -> None:
    """`ACCEPT_PATH_NODES` is the list of workflow nodes greyed out
    when the validator rejects a review. A missing reviewer here
    would leave its circle stuck blue + spinning after a rejection."""
    accept_path = _extract_js_array("ACCEPT_PATH_NODES", html_text)
    assert accept_path, "JS const ACCEPT_PATH_NODES not found in template"
    for node in _SECURITY_NODES:
        assert node in accept_path, (
            f"ACCEPT_PATH_NODES missing {node!r}; without this the circle "
            f"stays blue when validate_request rejects the run"
        )


def test_handle_validation_result_cascades_every_reviewer(html_text: str) -> None:
    """REGRESSION GUARD for the ConfigurationReviewer bug: the cascade
    inside `handleValidationResult` (accept branch) must call
    `markActive(...)` for every reviewer. The list is hard-coded
    rather than derived from SECURITY_NODES, so it's easy to forget
    a new entry — that's exactly what happened when Configuration
    was added: its circle never went blue + spinner during live runs."""
    # Pin down the relevant slice rather than searching the whole file
    # — `handleValidationResult` is the one place where the four
    # reviewers are spelled out alongside `"security_reviewers"`.
    m = re.search(
        r"function\s+handleValidationResult[^{]*\{(.*?)\n\s*\}",
        html_text,
        re.DOTALL,
    )
    assert m, "handleValidationResult function not found in template"
    body = m.group(1)
    for node in _SECURITY_NODES:
        assert node in body, (
            f"handleValidationResult cascade missing {node!r}; the "
            f"workflow circle for this reviewer will stay grey instead "
            f"of going blue + spinner when validate_request accepts. "
            f"See templates/index.html, the `.forEach(markActive)` line."
        )
