## Findings (ground truth)

- [dependency] requirements.txt — flask — [critical] CVE-2023-30861 session cookie hijack

## Proposed TL;DR (under review)

The review surfaced critical issues in three packages. flask (CVE-2023-30861)
needs an immediate session-handler upgrade; django (CVE-2025-99999) shows a
known RCE in the same template engine; and the legacy code in src/auth.py
contains a hardcoded admin token.

Block deploy until all three are patched.
