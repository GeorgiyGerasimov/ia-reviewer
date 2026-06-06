"""Heuristic file classifier — purpose-based, not extension-based.

`LLMPerFileReviewer.PATH_PATTERNS` already filters by extension (drop
binary blobs, lockfiles, etc). This classifier complements that by
identifying the *role* a file plays in the project, which lets each
reviewer skip categories that don't pay back the LLM call:

  - `Injection` reviewer would waste a call looking for SQL injection
    in `docs/architecture.md` — markdown does not execute.
  - `OWASP` reviewer would waste a call applying A02 cryptography
    rules to `tests/unit/test_crypto.py` — test fixtures use known-bad
    keys on purpose.

Categories:

  - **CORE** — production source. The default; full review.
  - **TEST** — pytest / jest / rspec / go-test files. Most reviewers
    should skip; secrets-scanning may want them later (PR follow-up).
  - **DOCS** — markdown, rst, asciidoc; under `docs/` or top-level.
  - **INFRA** — Dockerfile, compose, terraform, k8s, github workflows,
    Makefile, nginx.conf. OWASP A05 cares; injection / dep do not.
  - **VENDORED** — node_modules, .venv, dist/build/target, __pycache__.
    No reviewer should touch.
  - **GENERATED** — `*.min.*`, `*_pb2.py`, `*.pb.go`, lockfiles.

Conservative default: when no heuristic matches, return CORE. Better
to scan an unrecognised file than to silently skip what might be
production code.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath


class FileCategory(StrEnum):
    """Purpose-based file classification. `StrEnum` makes the enum
    value (e.g. `"core"`) string-comparable + json-encodable without
    extra wiring — convenient for emitting in progress envelopes."""

    CORE = "core"
    TEST = "test"
    DOCS = "docs"
    INFRA = "infra"
    VENDORED = "vendored"
    GENERATED = "generated"


# Ordered evaluation — first hit wins. The order matters: VENDORED and
# GENERATED outrank TEST (vendored test files are still vendored), TEST
# outranks CORE (a test file deep inside src/ is still a test).

# Vendored directories — exact path segments that anywhere in the path
# disqualify a file. (Examples cover the common ecosystems.)
_VENDORED_DIRS = frozenset({
    "node_modules", "vendor",
    ".venv", "venv", ".env",  # virtualenvs / vendored env directories
    "__pycache__", ".pytest_cache", ".tox",
    "dist", "build", "target",
    ".next", ".nuxt", ".cache",
    "coverage", "htmlcov",
})

# Generated files — by basename pattern.
_GENERATED_BASENAME_PATTERNS = (
    re.compile(r".*\.min\.(js|css|map)$"),
    re.compile(r".*_pb2(_grpc)?\.py$"),
    re.compile(r".*\.pb\.go$"),
    re.compile(r".*\.pb\.cc$"),
    re.compile(r"(package|yarn|poetry|Pipfile|Cargo|composer|Gemfile)\.lock$"),
    re.compile(r"package-lock\.json$"),
)

# Test files — by basename pattern OR directory.
_TEST_BASENAME_PATTERNS = (
    re.compile(r"^test_.*\.py$"),
    re.compile(r".*_test\.py$"),
    re.compile(r".*_test\.go$"),
    re.compile(r".*\.test\.(js|jsx|ts|tsx)$"),
    re.compile(r".*\.spec\.(js|jsx|ts|tsx|rb)$"),
    re.compile(r"^conftest\.py$"),
)
_TEST_DIRS = frozenset({"tests", "test", "__tests__", "spec", "e2e"})

# Docs — by extension or by directory.
_DOCS_EXTENSIONS = frozenset({".md", ".rst", ".adoc", ".txt"})
_DOCS_DIRS = frozenset({"docs", "doc", "documentation", "wiki"})

# Infra — by basename glob (case-sensitive: Dockerfile is canonical).
_INFRA_BASENAME_PATTERNS = (
    re.compile(r"^Dockerfile(\..+)?$"),
    re.compile(r"^docker-compose(\..+)?\.ya?ml$"),
    re.compile(r"^Makefile$"),
    re.compile(r"^nginx\.conf$"),
)
_INFRA_EXTENSIONS = frozenset({".tf"})
_INFRA_DIRS = frozenset({"k8s", "kubernetes", "deploy", "deployment"})

# Code-source directories (anchor for CORE) — used to disambiguate;
# everything inside that ISN'T a more-specific category is CORE.
_CORE_DIRS = frozenset({"src", "lib", "app", "pkg", "internal", "cmd"})


def classify_file(path: str) -> FileCategory:
    """Classify `path` (POSIX-style, snapshot-relative) into a
    `FileCategory`. Pure function — no I/O, no side effects.

    Order matters: more-specific categories beat less-specific ones.
    """
    p = PurePosixPath(path)
    parts = p.parts
    parts_set = set(parts)
    name = p.name

    # 1. Vendored — anywhere in the path. Wins over everything else
    #    because vendored stuff is by definition not our code.
    if parts_set & _VENDORED_DIRS:
        return FileCategory.VENDORED

    # 2. Generated — by basename.
    for pat in _GENERATED_BASENAME_PATTERNS:
        if pat.match(name):
            return FileCategory.GENERATED

    # 3. Test — by basename OR by directory.
    if parts_set & _TEST_DIRS:
        return FileCategory.TEST
    for pat in _TEST_BASENAME_PATTERNS:
        if pat.match(name):
            return FileCategory.TEST

    # 4. Docs — by directory OR by extension.
    if parts_set & _DOCS_DIRS:
        return FileCategory.DOCS
    if p.suffix.lower() in _DOCS_EXTENSIONS:
        return FileCategory.DOCS

    # 5. Infra.
    for pat in _INFRA_BASENAME_PATTERNS:
        if pat.match(name):
            return FileCategory.INFRA
    if p.suffix.lower() in _INFRA_EXTENSIONS:
        return FileCategory.INFRA
    if parts_set & _INFRA_DIRS:
        return FileCategory.INFRA
    # `.github/workflows/*.yml|yaml` — narrow match (not all yaml is infra).
    if len(parts) >= 3 and parts[0] == ".github" and parts[1] == "workflows":
        return FileCategory.INFRA

    # 6. Fallback — anything else is treated as CORE (production code).
    #    This includes both explicit src/lib/app/pkg trees AND
    #    unrecognised paths — conservative default per module docstring.
    _ = _CORE_DIRS  # referenced for clarity; classification is via fall-through
    return FileCategory.CORE
