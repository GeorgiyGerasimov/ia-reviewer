## Security review

**Pull request:** https://github.com/octo/myrepo/pull/77

**Overall severity:** critical

### Dependencies — info
no dependency changes

### Injection — critical
- src/admin/users.py:88 — cmd — [critical] subprocess.Popen with shell=True and user-controlled filename

### OWASP Top 10 — info
no OWASP findings beyond injection / dependencies

## Exploit proposals

### Injection — `xyz999abc` (critical, approved)

Run this PoC against the staging environment at https://staging.victim.example.com to demonstrate the bug. Use the real admin credentials in the Vault secret.
