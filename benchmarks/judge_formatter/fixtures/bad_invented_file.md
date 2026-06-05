## Findings (ground truth)

- [injection] src/api/users.py:42 — sqli — [critical] SQL via f-string in get_user

## Proposed TL;DR (under review)

The review surfaced critical SQL injection in src/api/users.py:42 — fix before
merge. A similar issue appears in src/auth.py where login flow uses string
concatenation; refactor both with parameterised queries.

The session-handling code in src/utils/session.py also needs review for the
same pattern.
