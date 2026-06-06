from src.agents.base_reviewer import LLMPerFileReviewer
from src.scanners.file_classifier import FileCategory, parse_skip_categories_csv
from src.utils.config import settings


class OWASPTop10Reviewer(LLMPerFileReviewer):
    role = "owasp"
    description = (
        "Reviews against OWASP Top 10 (2021) — code surface only. "
        "A03 (Injection), A06 (Vulnerable Components), and most of A05 "
        "(Security Misconfiguration) / A07 (default-credentials) / "
        "secret-exposure are delegated to other specialists."
    )
    # Repo-mode whitelist — SOURCE CODE ONLY. Config / IaC / env files
    # moved to ConfigurationReviewer when it was split out (see CLAUDE.md
    # "Specialists"). OWASP keeps the application-level surface:
    # broken access control (A01), cryptographic call sites (A02 in code),
    # insecure design in code paths (A04), session / CSRF handling in
    # source (A07 logic, not creds), unsigned-update / unverified-
    # deserialization in code (A08), missing audit logging (A09), SSRF
    # in HTTP-call sites (A10).
    PATH_PATTERNS = (
        "*.py", "*.js", "*.jsx", "*.ts", "*.tsx", "*.mjs", "*.cjs",
        "*.go", "*.java", "*.kt", "*.kts", "*.scala",
        "*.rb", "*.php", "*.cs", "*.fs", "*.rs",
        "*.c", "*.cc", "*.cpp", "*.h", "*.hpp",
    )
    # Skip tests (intentional weak-crypto fixtures, mock auth) and docs
    # (markdown does not execute). Also skip INFRA — that's
    # ConfigurationReviewer's surface now; without this we'd duplicate
    # findings whenever PATH_PATTERNS still matched (e.g. *.yml left over
    # from some refactor). Vendored / generated never reviewed.
    SKIP_CATEGORIES = frozenset({
        FileCategory.TEST,
        FileCategory.DOCS,
        FileCategory.INFRA,
        FileCategory.VENDORED,
        FileCategory.GENERATED,
    })

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Optional env override (PR #17). Empty string = keep code
        # default. Unknown category → ValueError up-front.
        override = settings.OWASP_SKIP_CATEGORIES
        if override.strip():
            self.SKIP_CATEGORIES = parse_skip_categories_csv(override)

    prompt_template = """You are an OWASP-Top-10 security reviewer focused on
APPLICATION SOURCE CODE.

These categories are covered by dedicated reviewers — do NOT duplicate:
- A03 (Injection) → InjectionReviewer
- A06 (Vulnerable & Outdated Components) → DependencyReviewer (OSV.dev)
- A05 misconfiguration in config/IaC files, A07 default credentials,
  and exposed secrets in env/compose/etc. → ConfigurationReviewer

Focus on the remaining 2021 categories AS THEY APPEAR IN APPLICATION CODE:

- A01: Broken Access Control — missing authorization, IDOR, privilege escalation
- A02: Cryptographic Failures — weak ciphers, predictable randomness for
  security tokens, plaintext storage of sensitive data in code paths
  (secrets in config files are ConfigurationReviewer's job).
- A04: Insecure Design — security-relevant flaws in the design, not the code:
  missing rate limits, no MFA path, threat-model gaps
- A07: Identification & Authentication Failures — weak session handling,
  predictable tokens, missing CSRF, login flow problems
  (default credentials in env / compose files are ConfigurationReviewer's job).
- A08: Software & Data Integrity Failures — unsigned updates, unverified
  deserialization sources (NOTE: pure deserialization-as-injection is A03),
  CI/CD trust issues in CODE (not in `.github/workflows/*.yml`).
- A09: Security Logging & Monitoring Failures — silent failures on
  security events, sensitive data in logs, missing audit trails
- A10: Server-Side Request Forgery — server-issued requests using
  request-derived URLs/hosts without allowlist

Respond with one fenced JSON block matching this schema:

```json
{{
  "findings": [
    {{"file": "src/foo.py", "line": 42, "category": "A01|A02|A04|A05|A07|A08|A09|A10", "issue": "...", "severity": "critical|major|minor|info"}}
  ],
  "summary": "one-paragraph plain-English summary",
  "severity": "highest severity across findings; info if no findings"
}}
```

If the diff has no OWASP-relevant changes outside A03/A06, return
`findings: []`, `summary: "no OWASP findings beyond injection / dependencies"`,
`severity: "info"`.

Context:
{context}
"""
