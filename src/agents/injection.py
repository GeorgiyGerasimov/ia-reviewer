from src.agents.base_reviewer import LLMPerFileReviewer
from src.scanners.file_classifier import FileCategory


class InjectionReviewer(LLMPerFileReviewer):
    role = "injection"
    description = "Reviews code for injection vulnerabilities — SQLi, command, template, deserialization, XSS."
    # Repo-mode whitelist — source files only. Config/infra is out of scope
    # for injection (those go to OWASP). SQL files included because raw SQL
    # often hides injection sinks expressed in stored procedures.
    PATH_PATTERNS = (
        "*.py", "*.js", "*.jsx", "*.ts", "*.tsx", "*.mjs", "*.cjs",
        "*.go", "*.java", "*.kt", "*.kts", "*.scala",
        "*.rb", "*.php", "*.cs", "*.fs", "*.rs",
        "*.c", "*.cc", "*.cpp", "*.h", "*.hpp",
        "*.sql", "*.sh", "*.bash",
    )
    # Test files contain intentional injection-pattern examples
    # (f-string SQL in test_validator.py, subprocess invocations in
    # test fixtures, etc) — reviewing them produces noise. Docs do not
    # execute. Infra is OWASP's responsibility (A05). Vendored /
    # generated are not our code.
    SKIP_CATEGORIES = frozenset({
        FileCategory.TEST,
        FileCategory.DOCS,
        FileCategory.INFRA,
        FileCategory.VENDORED,
        FileCategory.GENERATED,
    })
    prompt_template = """You are an injection-vulnerability reviewer.

Examine the diff for code paths that take attacker-controlled input and pass
it into an interpreter, query engine, or sink without proper escaping or
parameterization. Focus on:

- SQL/NoSQL injection — f-strings, % formatting, string concatenation, or
  template substitution building query text from request-derived values
- Command injection — `subprocess.*(..., shell=True)`, `os.system`, `exec`,
  `eval`, backtick-style shells; any place where user data hits a shell
- Template / SSTI — Jinja, Mako, Handlebars, etc. rendered with untrusted data
- Deserialization — pickle.loads, yaml.load (non-safe), unmarshal of untrusted
  bytes, JSON → object hydration without schema
- Path traversal — file paths concatenated with request data without normalization
- XSS / HTML injection — server-side rendering or response building that
  emits unescaped request data; `innerHTML`, `dangerouslySetInnerHTML`,
  `document.write` on the client side
- LDAP / XPath / regex injection of similar shape

Respond with one fenced JSON block matching this schema:

```json
{{
  "findings": [
    {{"file": "src/foo.py", "line": 42, "category": "sqli|cmd|template|deserialization|path|xss|other", "issue": "...", "severity": "critical|major|minor|info"}}
  ],
  "summary": "one-paragraph plain-English summary",
  "severity": "highest severity across findings; info if no findings"
}}
```

If the diff has no injection-relevant code paths, return `findings: []`,
`summary: "no injection-relevant changes"`, `severity: "info"`.

Context:
{context}
"""
