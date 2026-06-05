## Findings (ground truth)

- [dependency] requirements.txt — flask — [critical] CVE-2023-30861 session cookie hijack
- [dependency] requirements.txt — jinja2 — [major] CVE-2024-22195 SSTI on autoescape disabled

## Proposed TL;DR (under review)

Two dependency vulnerabilities were surfaced from requirements.txt. CVE-2023-30861
in flask is a critical session-cookie hijack that requires upgrading flask to a
patched release before deploy.

The major CVE-2024-22195 in jinja2 only triggers when autoescape is explicitly
disabled. Upgrade jinja2 alongside the flask bump to close both.
