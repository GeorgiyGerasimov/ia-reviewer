---
name: venv-policy
description: Python dependency policy for ia-reviewer. All deps live in .venv/ at project root. Never install into system Python or use /opt/homebrew/bin/python3.11 directly. Apply before running any pytest, ruff, mypy, or python command.
---

# Virtual Environment Policy

## Rule

All Python dependencies must live in `.venv/` at the project root. Never install into the system Python or any global location.

## Why

Project isolation. Different projects on this machine use different LangChain/LangGraph/httpx versions. Global installs cause version conflicts and make CI/Docker behaviour diverge from local.

## First-time setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt -e .
```

## How to invoke Python tools

Every Python command must go through `.venv`:

- `.venv/bin/python ...`
- `.venv/bin/pytest ...`
- `.venv/bin/ruff ...`
- `.venv/bin/mypy ...`

Or `source .venv/bin/activate` once per shell.

**Never** invoke `/opt/homebrew/bin/python3.11` or `/usr/bin/python3` directly — use `.venv/bin/python` instead.

## If `.venv` is missing

Create it before running any test/lint command. Don't fall back to system Python.

## Parity with CI and Docker

- CI (when added) must create a fresh `.venv` per job — local and CI must stay in parity
- If a Dockerfile is added, it should install from `requirements.txt` — keep that file as the source of truth
- `.venv/` is already in `.gitignore` — don't commit it
