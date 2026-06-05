## Findings (ground truth)

- [owasp] src/middleware.py — A05 — [minor] DEBUG=True in production config

## Proposed TL;DR (under review)

The review found one minor configuration issue in src/middleware.py: DEBUG=True
is set in the production config. Driving the flag from an environment variable
closes the finding.
