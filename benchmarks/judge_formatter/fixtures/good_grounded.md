## Findings (ground truth)

- [injection] src/api/users.py:42 — sqli — [critical] SQL via f-string in get_user
- [injection] src/api/auth.py:88 — xss — [major] unescaped user input in error template
- [owasp] config/production.py:4 — [critical] hardcoded DB password

## Proposed TL;DR (under review)

The review surfaced two critical issues: a SQL injection in src/api/users.py:42
that needs immediate parameterisation, and a hardcoded production database
password in config/production.py:4 that blocks any safe credential rotation.

A non-critical XSS in src/api/auth.py:88 can be addressed with a template
helper. Recommend blocking merge until the SQL fix and credential refactor
land.
