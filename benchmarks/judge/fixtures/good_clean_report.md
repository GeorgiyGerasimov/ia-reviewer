## Security review

**Pull request:** https://github.com/octo/myrepo/pull/42

**Overall severity:** major

### Dependencies — minor
1 vulnerability finding across 2 packages; highest severity minor. The package `axios` 0.21.0 is affected by CVE-2020-28168 (SSRF), fixed in 0.21.1.

- package.json — axios — [minor] SSRF via baseURL handling, fixed in 0.21.1

### Injection — major
no findings beyond previously reviewed material

- src/auth/login.py:42 — sqli — [major] SQL via f-string in login flow; switch to parameterised query

### OWASP Top 10 — info
no OWASP findings beyond injection / dependencies
