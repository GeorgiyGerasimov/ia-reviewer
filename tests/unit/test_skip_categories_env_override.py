"""Env-override for per-role `SKIP_CATEGORIES`.

Code default ships sensible opinions (injection skips
test/docs/infra/vendored/generated; owasp keeps infra). Operators
running niche repos sometimes need to override that — e.g. a security
team scanning a docs-only repo for embedded credentials wants
`INJECTION_SKIP_CATEGORIES=""` (skip nothing).

Contract:
  * `INJECTION_SKIP_CATEGORIES` (env CSV) overrides
    `InjectionReviewer.SKIP_CATEGORIES` ON INSTANCE CREATION.
  * Same for `OWASP_SKIP_CATEGORIES`.
  * Empty env value = use code default (frozenset).
  * Whitespace and case insensitive: "Test, DOCS , vendored".
  * Unknown category name → ValueError on instance construction
    (loud, not silent — bad config should fail fast).
  * Specifying "" explicitly as the env value means "scan everything"
    (empty frozenset) — different from "env not set".
"""

import pytest

from src.agents.injection import InjectionReviewer
from src.agents.owasp import OWASPTop10Reviewer
from src.scanners.file_classifier import FileCategory

# ── default behaviour (env unset / blank) ──────────────────────────────


def test_injection_keeps_code_default_when_env_blank(mocker):
    """Empty env → keep the hardcoded opinionated defaults."""
    mocker.patch("src.agents.injection.settings.INJECTION_SKIP_CATEGORIES", "")
    r = InjectionReviewer()
    # The code default from PR #16
    assert frozenset({
        FileCategory.TEST,
        FileCategory.DOCS,
        FileCategory.INFRA,
        FileCategory.VENDORED,
        FileCategory.GENERATED,
    }) == r.SKIP_CATEGORIES


def test_owasp_keeps_code_default_when_env_blank(mocker):
    mocker.patch("src.agents.owasp.settings.OWASP_SKIP_CATEGORIES", "")
    r = OWASPTop10Reviewer()
    assert frozenset({
        FileCategory.TEST,
        FileCategory.DOCS,
        FileCategory.VENDORED,
        FileCategory.GENERATED,
    }) == r.SKIP_CATEGORIES


# ── env override ───────────────────────────────────────────────────────


def test_injection_env_csv_overrides_default(mocker):
    mocker.patch(
        "src.agents.injection.settings.INJECTION_SKIP_CATEGORIES",
        "vendored,generated",
    )
    r = InjectionReviewer()
    # New, narrower skip set — TEST/DOCS/INFRA are now in scope.
    assert frozenset({
        FileCategory.VENDORED,
        FileCategory.GENERATED,
    }) == r.SKIP_CATEGORIES


def test_owasp_env_single_category_overrides_default(mocker):
    mocker.patch(
        "src.agents.owasp.settings.OWASP_SKIP_CATEGORIES",
        "vendored",
    )
    r = OWASPTop10Reviewer()
    assert frozenset({FileCategory.VENDORED}) == r.SKIP_CATEGORIES


def test_whitespace_and_case_insensitive(mocker):
    mocker.patch(
        "src.agents.injection.settings.INJECTION_SKIP_CATEGORIES",
        " Test ,   DOCS,vendored",
    )
    r = InjectionReviewer()
    assert frozenset({
        FileCategory.TEST,
        FileCategory.DOCS,
        FileCategory.VENDORED,
    }) == r.SKIP_CATEGORIES


# ── bad input fails loudly ─────────────────────────────────────────────


def test_unknown_category_raises_on_construction(mocker):
    """Bad config should crash at startup, not silently fall back —
    otherwise an operator typos `INJECTION_SKIP_CATEGORIES=tets,docs`
    and wastes hours wondering why test files are still being scanned."""
    mocker.patch(
        "src.agents.injection.settings.INJECTION_SKIP_CATEGORIES",
        "test,nonexistent_category",
    )
    with pytest.raises(ValueError, match="nonexistent_category"):
        InjectionReviewer()
