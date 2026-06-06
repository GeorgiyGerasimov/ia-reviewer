"""Unit tests for per-specialist path filters used in repo-mode review.

Each reviewer carries a `PATH_PATTERNS` tuple (fnmatch-style basenames).
`_matches_path` returns True iff the file's basename matches any pattern.
These filters decide which files in `state.request.repo_files` get fetched
and sent through the per-file LLM call.
"""

from src.agents.dependency import DependencyReviewer
from src.agents.injection import InjectionReviewer
from src.agents.owasp import OWASPTop10Reviewer


def test_dependency_reviewer_path_filter():
    """Dependency reviewer: manifests and lockfiles only."""
    assert DependencyReviewer._matches_path("package.json")
    assert DependencyReviewer._matches_path("frontend/package-lock.json")
    assert DependencyReviewer._matches_path("requirements.txt")
    assert DependencyReviewer._matches_path("pyproject.toml")
    assert DependencyReviewer._matches_path("go.mod")
    assert DependencyReviewer._matches_path("Cargo.lock")
    # Source code is out of scope for dependency reviewer
    assert not DependencyReviewer._matches_path("src/auth.py")
    assert not DependencyReviewer._matches_path("README.md")


def test_injection_reviewer_path_filter():
    """Injection reviewer: source files only (no manifests, no docs, no config)."""
    assert InjectionReviewer._matches_path("src/auth.py")
    assert InjectionReviewer._matches_path("server.js")
    assert InjectionReviewer._matches_path("app/Application.java")
    assert InjectionReviewer._matches_path("src/handlers/foo.go")
    # Docs and config are out of scope for injection
    assert not InjectionReviewer._matches_path("README.md")
    assert not InjectionReviewer._matches_path("Dockerfile")
    assert not InjectionReviewer._matches_path("package.json")


def test_owasp_reviewer_path_filter():
    """OWASP reviewer (after Configuration was split out): source files
    only. Infra / IaC / env moved to ConfigurationReviewer — checked
    separately in `test_configuration_reviewer.py`."""
    # Source — same surface as Injection
    assert OWASPTop10Reviewer._matches_path("src/foo.go")
    assert OWASPTop10Reviewer._matches_path("server.js")
    assert OWASPTop10Reviewer._matches_path("controllers/users.py")
    # Config / IaC / env explicitly NOT in scope here anymore —
    # ConfigurationReviewer handles them; overlap = duplicate findings.
    assert not OWASPTop10Reviewer._matches_path("Dockerfile")
    assert not OWASPTop10Reviewer._matches_path("k8s/deploy.yaml")
    assert not OWASPTop10Reviewer._matches_path("main.tf")
    assert not OWASPTop10Reviewer._matches_path(".env.production")
    # Still out of scope: docs and manifests
    assert not OWASPTop10Reviewer._matches_path("README.md")
    assert not OWASPTop10Reviewer._matches_path("package.json")
