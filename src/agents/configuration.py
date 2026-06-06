from src.agents.base_reviewer import LLMPerFileReviewer
from src.scanners.file_classifier import FileCategory, parse_skip_categories_csv
from src.utils.config import settings


class ConfigurationReviewer(LLMPerFileReviewer):
    """Fourth security specialist — focuses on configuration-surface
    files: containers, compose, env files, IaC (Terraform), nginx,
    generic INI/YAML/TOML/properties.

    Why a dedicated reviewer instead of folding into OWASP:
      * In real reports, findings about default credentials / debug
        flags / exposed secrets / permissive CORS were overwhelming
        the OWASP block and crowded out A01/A02/A04/A07 findings on
        source code. Splitting them into their own section makes the
        report scan-readable by category.
      * Config-surface review needs a different prompt — the model
        should look at *settings* (key=value, env defaults, exposed
        ports, granted roles, CORS allowlists) rather than at *call
        flow*. A single shared prompt for both made each one weaker.

    Boundaries with siblings:
      * Source code (Python / JS / Go / etc.) → InjectionReviewer or
        OWASPTop10Reviewer; ConfigurationReviewer does NOT scan it.
      * Package manifests (`package.json`, `requirements.txt`,
        `pyproject.toml` *for deps*, etc.) → DependencyReviewer via
        OSV.dev. We DO scan `pyproject.toml` ourselves but for tool
        config (e.g. `[tool.foo]` keys), not for the dep tree.
      * A05 Misconfiguration and A07 default-credentials surface
        items historically claimed by OWAS-prompt are now in scope
        HERE; OWASPTop10Reviewer's prompt was tightened to delegate.
    """

    role = "configuration"
    description = (
        "Reviews configuration / IaC / env files for misconfigurations, "
        "weak or default credentials, exposed secrets, debug flags."
    )

    # Repo-mode whitelist — strictly config-surface. Anything that
    # looks like source code is intentionally OUT of scope (those go
    # to Injection / OWASP / Dependency).
    PATH_PATTERNS = (
        # Containers
        "Dockerfile", "Dockerfile.*", "*.dockerfile",
        "docker-compose*.yml", "docker-compose*.yaml",
        ".dockerignore",
        # Env / dotfiles
        ".env", ".env.*",
        # Web-server / proxy
        "nginx.conf", "*.nginx",
        # IaC
        "*.tf", "*.tfvars",
        # Generic config
        "*.yml", "*.yaml",
        "*.toml",
        "*.ini",
        "*.conf", "*.cfg",
        "*.properties",
    )

    # Skip TEST (fixtures with intentional fake secrets), DOCS (md
    # examples), VENDORED (third-party configs we don't own),
    # GENERATED (lockfiles etc. — covered by Dependency). Keep INFRA
    # — that's literally the surface we're here for. CORE too — some
    # legitimate config files (`pyproject.toml`, top-level `*.toml`)
    # classify as CORE.
    SKIP_CATEGORIES = frozenset({
        FileCategory.TEST,
        FileCategory.DOCS,
        FileCategory.VENDORED,
        FileCategory.GENERATED,
    })

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Symmetric with InjectionReviewer / OWASPTop10Reviewer:
        # optional env-driven override of which categories to skip.
        # Empty = keep code default. Unknown category name → ValueError
        # at construction so a typo crashes startup, not the review.
        override = getattr(settings, "CONFIGURATION_SKIP_CATEGORIES", "")
        if override and override.strip():
            self.SKIP_CATEGORIES = parse_skip_categories_csv(override)

    prompt_template = """You are a security reviewer focused on CONFIGURATION,
infrastructure-as-code, and secret-handling. Your scope:

- Misconfigurations: debug=true in production paths, verbose error pages
  in prod, permissive CORS (`*` or unbounded origin lists), open admin
  surfaces, exposed management ports, missing auth on internal endpoints.
- Default / weak credentials: `admin/admin`, `root` with no password,
  shipped sample credentials left active, default API keys / tokens.
- Exposed secrets: hardcoded keys / tokens / passwords / connection
  strings in compose files, env defaults, or committed `.env` files;
  TLS certs / private keys checked in; secrets in container ENV that
  end up in image layers.
- Container misconfig: `--privileged`, host-network, root user inside
  the image, mounting the docker socket, missing `read_only`, world-
  writable volumes.
- IaC issues: overly permissive IAM policies, public S3 buckets,
  unencrypted volumes / databases, security groups with 0.0.0.0/0 on
  sensitive ports, missing flow logs.
- Reverse-proxy misconfig: missing security headers, weak TLS settings,
  unrestricted forwarding, header smuggling vectors.

OUT of scope (handled by other reviewers — do NOT duplicate):
- SQL/command/template injection in source code → InjectionReviewer.
- CVEs in third-party packages → DependencyReviewer (OSV.dev).
- Application-level authentication logic in source code → OWASPTop10Reviewer.

Respond with one fenced JSON block matching this schema:

```json
{{
  "findings": [
    {{
      "file": "docker-compose.yml",
      "line": 12,
      "category": "misconfig|default_creds|exposed_secret|container|iac|proxy|other",
      "issue": "...",
      "severity": "critical|major|minor|info"
    }}
  ],
  "summary": "one-paragraph plain-English summary",
  "severity": "highest severity across findings; info if no findings"
}}
```

If the file has no configuration-relevant issues, return `findings: []`,
`summary: "no configuration findings"`, `severity: "info"`.

Context:
{context}
"""
