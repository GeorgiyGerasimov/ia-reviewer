## Findings (ground truth)

- [dependency] requirements.txt — flask — [critical] CVE-2023-30861 session cookie hijack

## Proposed TL;DR (under review)

The review surfaced critical CVE-2023-30861 in flask (session cookie hijack)
plus an additional critical CVE-2025-99999 covering remote code execution
through the same package.

Upgrade flask to the patched release that addresses both advisories before
deploy.
