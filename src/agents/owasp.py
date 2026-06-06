from src.agents.base_reviewer import LLMPerFileReviewer
from src.scanners.file_classifier import FileCategory


class OWASPTop10Reviewer(LLMPerFileReviewer):
    role = "owasp"
    description = "Reviews against OWASP Top 10 (2021), excluding A03 injection and A06 vulnerable components."
    # Repo-mode whitelist — source AND infra/config. OWASP needs to see code
    # (A01/A07 access control & auth), configs (A05 misconfiguration), and
    # IaC (A05 + A08 integrity).
    PATH_PATTERNS = (
        # Source — same set as injection so OWASP also sees real code paths
        "*.py", "*.js", "*.jsx", "*.ts", "*.tsx", "*.mjs", "*.cjs",
        "*.go", "*.java", "*.kt", "*.kts", "*.scala",
        "*.rb", "*.php", "*.cs", "*.fs", "*.rs",
        "*.c", "*.cc", "*.cpp", "*.h", "*.hpp",
        # Config / IaC / containers / env
        "*.yml", "*.yaml", "*.toml", "*.ini", "*.conf", "*.cfg",
        "Dockerfile", "Dockerfile.*", "*.dockerfile",
        "docker-compose*.yml", "docker-compose*.yaml",
        "*.tf", "*.tfvars",
        ".env", ".env.*",
        "nginx.conf", "*.nginx",
        "*.properties",
    )
    # Skip tests (intentional weak-crypto fixtures, mock auth) and docs
    # (markdown does not execute). Keep INFRA — that's the A05/A07/A08
    # surface. Vendored / generated never reviewed.
    SKIP_CATEGORIES = frozenset({
        FileCategory.TEST,
        FileCategory.DOCS,
        FileCategory.VENDORED,
        FileCategory.GENERATED,
    })
    prompt_template = """You are an OWASP-Top-10 security reviewer.

A03 (Injection) and A06 (Vulnerable & Outdated Components) are covered by
dedicated reviewers — do NOT duplicate their findings. Focus on the remaining
2021 categories:

- A01: Broken Access Control — missing authorization, IDOR, privilege escalation
- A02: Cryptographic Failures — weak/missing TLS, plaintext secrets, weak ciphers,
  predictable randomness for security tokens
- A04: Insecure Design — security-relevant flaws in the design, not the code:
  missing rate limits, no MFA path, threat-model gaps
- A05: Security Misconfiguration — debug flags in prod paths, default creds,
  permissive CORS, verbose errors, exposed admin surface
- A07: Identification & Authentication Failures — weak session handling,
  predictable tokens, missing CSRF, login flow problems
- A08: Software & Data Integrity Failures — unsigned updates, unverified
  deserialization sources (NOTE: pure deserialization-as-injection is A03),
  CI/CD trust issues
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
