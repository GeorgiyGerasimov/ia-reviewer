"""ConfigurationReviewer — fourth security specialist, focused on
infra-as-code, container configs, env files, and exposed secrets.

Why a separate reviewer (instead of folding into OWASP A05):
  * Configuration findings (default credentials, debug flags, exposed
    secrets, permissive CORS, plaintext keys in compose files) were
    dominating the OWASP block in real reports and made it hard to
    see A01/A02/A04/etc. findings amid the noise.
  * Configuration-surface files (`.env`, `Dockerfile`, `docker-compose.yml`,
    `*.tf`, `nginx.conf`) need a different prompt than source code —
    the model should look at *settings* not at *call flow*. Splitting
    the prompts makes both reviewers tighter.
  * Renders as its own column in the Summary table and its own section
    in the report — operators can scan-read by category.

The reviewer extends `LLMPerFileReviewer` (one LLM call per matched
file) — same machinery as Injection / OWASP.
"""

from __future__ import annotations

from src.agents.configuration import ConfigurationReviewer
from src.scanners.file_classifier import FileCategory


def test_role_name() -> None:
    """Stable role identifier used in scope filtering, RAG, and the
    Summary table. Must NOT collide with any existing role."""
    assert ConfigurationReviewer.role == "configuration"


def test_path_patterns_match_infra_and_config_files() -> None:
    """PATH_PATTERNS must cover the canonical config/IaC surface:
    container files, compose, env vars, Terraform, nginx, generic
    INI/TOML/YAML/properties. Anything that an operator would call
    "a config file" rather than "code"."""
    r = ConfigurationReviewer()
    matched = [
        "Dockerfile",
        "Dockerfile.prod",
        "docker-compose.yml",
        "docker-compose.override.yaml",
        ".env",
        ".env.production",
        "nginx.conf",
        "infra/main.tf",
        "infra/prod.tfvars",
        "config.yml",
        "values.yaml",
        "pyproject.toml",
        "settings.ini",
        "app.cfg",
        "logback.properties",
    ]
    for path in matched:
        assert r._matches_path(path), f"expected PATH match for {path}"


def test_path_patterns_reject_source_code() -> None:
    """ConfigurationReviewer must NOT scan source files — those go to
    Injection / OWASP. Overlap = duplicate findings + duplicate cost."""
    r = ConfigurationReviewer()
    not_matched = [
        "src/foo.py",
        "src/bar.js",
        "main.go",
        "lib/server.rs",
        "app/controllers/users_controller.rb",
    ]
    for path in not_matched:
        assert not r._matches_path(path), f"unexpected PATH match for {path}"


def test_skip_categories_drops_test_docs_vendored_generated_but_keeps_infra() -> None:
    """Defaults: skip files that the heuristic flags as test/docs/
    vendored/generated, but KEEP infra (CORE *and* INFRA are the
    surface — config files in `k8s/`, `.github/workflows/`, etc.
    classify as INFRA in the classifier and must be reviewed)."""
    skip = ConfigurationReviewer.SKIP_CATEGORIES
    assert FileCategory.TEST in skip
    assert FileCategory.DOCS in skip
    assert FileCategory.VENDORED in skip
    assert FileCategory.GENERATED in skip
    # NOT in skip — INFRA is what this reviewer is FOR.
    assert FileCategory.INFRA not in skip
    assert FileCategory.CORE not in skip


def test_inherits_per_file_strategy() -> None:
    """The reviewer must extend `LLMPerFileReviewer` so it picks up the
    per-file iteration machinery (parallelism, progress emitters,
    skip-empty accounting) — same plumbing as Injection / OWASP."""
    from src.agents.base_reviewer import LLMPerFileReviewer
    assert issubclass(ConfigurationReviewer, LLMPerFileReviewer)


def test_prompt_template_mentions_misconfig_and_secrets() -> None:
    """The prompt must steer the model toward the actual scope —
    misconfigurations, weak/default credentials, exposed secrets,
    debug flags — and NOT toward A03 injection / A06 dependencies
    (which are handled by other reviewers). Loose contract: just
    check the prompt text mentions the key topics so it's hard to
    accidentally re-purpose this prompt for something else."""
    prompt = ConfigurationReviewer.prompt_template.lower()
    assert "misconfig" in prompt or "configuration" in prompt
    assert "secret" in prompt or "credential" in prompt
    assert "default" in prompt
    # Hard-rule: don't duplicate Injection / Dependency surface.
    assert "{context}" in ConfigurationReviewer.prompt_template
