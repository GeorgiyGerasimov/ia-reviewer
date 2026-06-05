## Security review

**Pull request:** https://github.com/octo/myrepo/pull/120

### Dependencies — minor
no dependency changes worth a CVE.

### Injection — major
- src/forms.py:55 — sqli — [major] f-string into raw query

### OWASP Top 10 — info
no OWASP findings
