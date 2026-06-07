# Repo-mode review

This mode scans a full snapshot of a GitHub repository at a given ref.
Use it when there is no PR (the change has landed, or you want to audit
an external project), or when the PR is so large that the unified diff
is no longer useful.

## Flow

```
URL validation (sync in /review) ──→ clone ──→ tree walk
            │                          │            │
            │  → 400 on invalid        │            ▼
            │                          │      list[RepoFile]
            ▼                          ▼            │
       BackgroundTask           snapshots/      validate_request
                                 ia-review-…/        (tree-level rules)
                                                     │
                                                     ▼
                                       reviewers (per-file LLM calls)
                                                     │
                                                     ▼
                                                 publish
                                                     │
                                                     ▼
                                           reports/<thread_id>.md
```

1. `/review` endpoint canonicalizes the URL via
   [`normalize_repo_url`](../src/integrations/repo_fetcher.py) — strips
   `.git`, trailing slashes, query strings, `/tree/<ref>` and `/blob/<ref>/…`
   path segments. Extracts a `ref` from `/tree/`/`/blob/` when the
   caller didn't pass one explicitly. Returns 400 on malformed input
   **before** any background work starts.
2. `_run_repo_review` emits `{node:"clone_repo", status:"active"}` and
   calls `clone_repo(repo_url, ref)` — a shallow
   `git clone --depth=1 --branch=<ref> --single-branch` (the
   `--branch` flag is omitted when `ref` is empty or `HEAD`, so git
   picks the repo's default branch). Destination is
   `tempfile.mkdtemp(prefix="ia-review-", dir=settings.SNAPSHOTS_DIR)`.
3. `list_repo_files(snapshot_dir)` walks the tree, skips `.git/`, drops
   any blob larger than `MAX_FILE_BYTES=200_000`. The result lands in
   `state.request.repo_files`.
4. `validate_request` runs **repo-mode pure-code rules only** (no LLM
   judge): empty tree → `empty_repo`; tree size above
   `MAX_REPO_FILES_HARD=5000` → `oversized_repo`; otherwise accept.
5. Each reviewer filters `repo_files` through its own `PATH_PATTERNS`
   (fnmatch against basename), caps at `MAX_FILES_PER_AGENT=200`,
   reads file contents directly from disk
   (`Path(snapshot_dir) / repo_file.path`), and runs **one LLM call per
   file**. Findings are auto-tagged with the file's relative path.
   Truncation (cap exceeded) inserts an explicit
   `truncated: N files over cap of M were not scanned` note in the
   AgentReview summary.
6. `publish_report` writes the report under
   `<reports_dir>/<thread_id>.md` and broadcasts the link into the chat.
7. `_run_repo_review`'s `finally:` calls `cleanup_snapshot` — the
   tempdir is removed whether the graph completed cleanly or errored.

## Per-specialist whitelists

Each reviewer carries a `PATH_PATTERNS` tuple, matched fnmatch-style
against the file's basename (so `*.py` hits files at any directory
depth).

### Dependency

Manifests and lockfiles across the major ecosystems:

```
package.json, package-lock.json, yarn.lock, pnpm-lock.yaml, npm-shrinkwrap.json
requirements*.txt, Pipfile, Pipfile.lock, poetry.lock, pyproject.toml, setup.py, setup.cfg
go.mod, go.sum
Cargo.toml, Cargo.lock
Gemfile, Gemfile.lock
composer.json, composer.lock
pom.xml, build.gradle, build.gradle.kts, settings.gradle*, gradle.lockfile
*.csproj, *.fsproj, *.vbproj, packages.config
```

### Injection

Source files only. Configs and docs are out of scope.

```
*.py, *.js, *.jsx, *.ts, *.tsx, *.mjs, *.cjs
*.go, *.java, *.kt, *.kts, *.scala
*.rb, *.php, *.cs, *.fs, *.rs
*.c, *.cc, *.cpp, *.h, *.hpp
*.sql, *.sh, *.bash
```

### OWASP Top 10

Source files only (same set as Injection). A05 misconfiguration /
A07 default-credentials / secret-exposure are delegated to
**ConfigurationReviewer** below. The OWASP prompt focuses on
application-level findings: A01 / A02 / A04 / A07-logic / A08 / A09 / A10.

```
…all of Injection's source patterns…
```

### Configuration

The fourth specialist (split out of OWASP — see
[CLAUDE.md::Specialists](../CLAUDE.md)). Scans config / IaC / env
files for misconfigurations, weak/default credentials, exposed
secrets, container hardening, IaC issues, reverse-proxy headers:

```
Dockerfile, Dockerfile.*, *.dockerfile
docker-compose*.yml, docker-compose*.yaml, .dockerignore
.env, .env.*
nginx.conf, *.nginx
*.tf, *.tfvars
*.yml, *.yaml, *.toml, *.ini, *.conf, *.cfg, *.properties
```

## Caps & limits

| Setting | Default | What it caps |
|---|---|---|
| `MAX_FILE_BYTES` (in [`repo_fetcher.py`](../src/integrations/repo_fetcher.py)) | 200_000 | Per-file size during snapshot walk. Bigger blobs are dropped → no LLM call for them. |
| `MAX_FILES_PER_AGENT` | 200 | Per-reviewer iteration cap (one LLM call per file). Beyond this the reviewer's summary carries a `truncated: N files …` note. |
| `MAX_REPO_FILES_HARD` | 5000 | Total tree size hard cap; trees larger than this are rejected by the validator outright. |
| `MAX_CYCLES` (in [`review_decision.py`](../src/agents/review_decision.py)) | 3 | Initial pass + up to 2 human-approved reruns. Each Phase B `rerun` increments `state.cycle_count`. |
| `MAX_EXPLOIT_PROPOSALS` (in [`exploit_proposal.py`](../src/agents/exploit_proposal.py)) | 3 | Hard cap on findings the exploit branch will iterate through. Beyond this remaining findings get `skipped_cap_reached`. |

## Snapshot lifecycle

- **Location**: `tempfile.mkdtemp(prefix="ia-review-", dir=settings.SNAPSHOTS_DIR)`,
  i.e. `<project>/snapshots/ia-review-<random>/`. Project-local rather
  than `$TMPDIR` so:
  - Predictable path under Docker (where `/tmp` is ephemeral).
  - Easy to bind-mount via `-v ./snapshots:/app/snapshots` for inspection.
  - Survives a container restart of just the app service if you want
    to inspect a clone after a crash.
- **Created**: by the lifespan at startup (`mkdir(parents=True,
  exist_ok=True)`) so the first `clone_repo` doesn't race on it; the
  per-review subdir is created by `tempfile.mkdtemp` inside `clone_repo`.
- **Removed**: in `_run_repo_review`'s `finally:` block via
  `cleanup_snapshot` — whether the graph completed cleanly, errored, or
  was cancelled. The snapshot is needed by reviewers and by any Phase B
  re-review pass, all of which happen within a single `astream` call;
  resume-after-interrupt does NOT re-read files, so cleanup at the end
  of the BackgroundTask is safe.

## Auth

- **Public repos**: anonymous clone. No `GITHUB_TOKEN` required.
- **Private repos**: when `settings.GITHUB_TOKEN` is set, it's injected
  into the HTTPS URL as basic-auth
  (`https://<token>@github.com/owner/repo`). SSH-style URLs (`git@github.com:o/r.git`)
  are left untouched — git uses the system's SSH agent for those.

## `git` is a runtime dependency

`clone_repo` raises a clean `RuntimeError("git executable not found on
PATH …")` if `git` is missing. Check with `which git` on the host (or
inside the container) before reporting "repo review broken."

## Reports and the UI

The on-disk report path is **always** `<reports_dir>/<thread_id>.md`
(see [`settings.REPORTS_DIR`](configuration.md)). PR-mode mirrors the
same file there too, so `GET /reports/<thread_id>.md` works uniformly
for both modes. The UI fetches the file with exponential backoff
(300 → 600 → 1200 → 2400 → 4800 ms) starting when the
`publish_report` (or `notify_rejection`) progress event arrives, so a
transient race between write-and-broadcast doesn't lock the panel into
"Report not available."
