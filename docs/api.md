# HTTP & WebSocket API

Base URL in the examples: `http://localhost:8000`.

## `GET /` — UI

HTML form + chat + workflow diagram + final report panel. See
[ui.md](ui.md) for the frontend behavior.

## `GET /health` — liveness

```bash
curl -s http://localhost:8000/health
# {"status": "ok"}
```

## `GET /img.png` — donkey mascot

Serves `templates/img.png` as `image/png`. Used by `GET /` for the
header decoration.

## `POST /review` — trigger a security review

Fire-and-forget. Returns immediately with a `thread_id`; the actual
review runs as a FastAPI BackgroundTask.

### PR-mode request

```bash
curl -s -X POST http://localhost:8000/review \
  -H 'Content-Type: application/json' \
  -d '{"pr_url": "https://github.com/owner/repo/pull/42"}'
# 202 → {"status": "started", "thread_id": "<uuid>", "pr_url": "..."}
```

### Repo-mode request

```bash
curl -s -X POST http://localhost:8000/review \
  -H 'Content-Type: application/json' \
  -d '{"repo_url": "https://github.com/owner/repo", "ref": "main"}'
# 202 → {"status": "started", "thread_id": "<uuid>", "repo_url": "...", "ref": "main"}
```

### `scope` parameter (both modes)

Pass a subset of `["dependency", "injection", "owasp"]` to skip
reviewers. Empty list (default) runs all three.

```bash
curl -s -X POST http://localhost:8000/review \
  -d '{"pr_url": "...", "scope": ["injection", "dependency"]}'
```

### Repo URL normalization

`repo_url` is canonicalized server-side via
[`normalize_repo_url`](../src/integrations/repo_fetcher.py) before any
clone happens. The user can paste any GitHub URL — the server strips:

| Input | Canonical | Extracted ref |
|---|---|---|
| `https://github.com/o/r.git` | `https://github.com/o/r` | — |
| `https://github.com/o/r/` | `https://github.com/o/r` | — |
| `https://github.com/o/r/tree/develop` | `https://github.com/o/r` | `develop` |
| `https://github.com/o/r/blob/main/src/app.py` | `https://github.com/o/r` | `main` |
| `https://github.com/o/r?foo=1#section` | `https://github.com/o/r` | — |

If the URL also carried a `ref` via `/tree/<ref>` AND the caller passed
an explicit `ref` field, the explicit one wins (unless it's empty or
`HEAD`). Result is echoed back in the response so the client sees
exactly what will be cloned.

### Validation errors (400)

- Neither `pr_url` nor `repo_url` → `{"error": "one of pr_url or repo_url is required"}`
- Both `pr_url` and `repo_url` → `{"error": "mixed payload: …"}`
- `scope` not a list → `{"error": "scope must be a list of role names"}`
- Unknown role in `scope` → `{"error": "invalid scope roles: …; allowed: …"}`
- `repo_url` malformed → `{"error": "invalid repo_url: …"}`

Exceptions inside the BackgroundTask are logged, not surfaced over HTTP
(the client already got its 202).

## `GET /reports/{filename}` — fetch a saved report

```bash
curl -s http://localhost:8000/reports/<thread_id>.md
```

- 200 `text/markdown; charset=utf-8` on success.
- 400 `{"detail": "invalid filename"}` for any filename containing `/`,
  `\`, or starting with `.` — path-traversal guard.
- 404 `{"detail": "report not found"}` if no such file in `reports_dir`.

Both PR-mode and repo-mode publish write the same `<thread_id>.md` file,
so this endpoint serves either uniformly.

## `GET /reviews` — list persisted reviews

Returns the most recent persisted reviews ordered by `created_at` desc.
Pagination via `limit` (default 50) and `offset` (default 0). The
`report_markdown` column is **not** included — it's heavy. Fetch a
single review's full body via `GET /reviews/{thread_id}`.

```bash
curl -s 'http://localhost:8000/reviews?limit=20'
# [
#   {"thread_id": "…", "mode": "repo", "target_url": "https://github.com/o/r",
#    "ref": "main", "author": "bot", "validation_category": "accepted",
#    "validation_accepted": true, "overall_severity": "minor",
#    "finding_count": 3, "created_at": "...", "completed_at": null},
#   ...
# ]
```

Empty list (200 `[]`) when persistence is disabled
(`DATABASE_URL=""` or init failed). Lets the UI render the section
empty instead of erroring.

## `GET /reviews/{thread_id}` — single review with findings

```bash
curl -s http://localhost:8000/reviews/<thread_id>
# {
#   "thread_id": "...", "mode": "repo", "target_url": "...",
#   "report_markdown": "## Security review …",
#   "findings": [
#     {"id": 1, "role": "owasp", "file": "Dockerfile", "line": null,
#      "category": "A05", "severity": "major", "issue": "...", "raw": {...}},
#     ...
#   ]
# }
```

- **200** with body when found.
- **404** `{"detail": "review not found"}` when missing.
- **404** `{"detail": "review store not enabled"}` when `DATABASE_URL` is unset.

## `GET /chat/{thread_id}/history` — replay chat

```bash
curl -s http://localhost:8000/chat/<thread_id>/history
# [{"role": "user|agent|system", "text": "…", "timestamp": "<iso8601>"}, ...]
```

In-memory store; restart wipes it.

## `WS /ws/chat/{thread_id}` — live chat + progress

Duplex JSON channel tied to a `thread_id`.

### On connect

The server replays first the **progress events** from `ProgressStore`,
then the **chat history** from `ChatStore`. The progress replay is what
lets a late-connecting browser tab paint the workflow diagram into its
current state instead of an all-blank panel.

### Client → server

```json
{"text": "rerun: also check for SSRF in /redirect"}
```

When the graph is paused at an `interrupt()`, the next user message is
routed as `Command(resume=text)` instead of being broadcast as a chat
message. Parsers:

- `ReviewDecisionAgent` — text starting with `rerun` (case-insensitive)
  approves a Phase B re-review; whatever follows `rerun:` or `rerun `
  is the extra context.
- `ExploitProposalAgent` — `approve <finding_id>` or `decline <finding_id>`.
  Any other text counts as decline.

### Server → all clients on the thread

Two envelope shapes:

**Chat message**
```json
{"role": "user" | "agent" | "system", "text": "...", "timestamp": "..."}
```

**Progress event** (not stored as a chat line; UI handles it separately)
```json
{"type": "progress", "node": "<node-name>", "status": "fired" | "empty" | "rejected" | "active"}
```

Optional fields on the progress envelope:
- `accepted: bool` — present only for `node="validate_request"`, surfaces
  the validator's verdict so the UI can collapse the accept/reject
  branches immediately.
- A terminal `{node: "__done__"}` envelope (no `status`) is sent after
  the graph completes.

Node names match `data-node` attributes in the workflow diagram. Special
node `clone_repo` is emitted by `_run_repo_review` before the graph
starts (repo-mode only).

## Background-task wiring

`POST /review` returns 202 and dispatches one of:

- `_run_review(app, pr_url, scope, thread_id)` — PR mode.
- `_run_repo_review(app, repo_url, ref, scope, thread_id)` — repo mode.
  Wraps the LangGraph run with clone + cleanup, and a chat broadcast
  `Review complete. Full report: /reports/<thread_id>.md` once the graph
  finishes (skipped while interrupts are pending).

The chat WebSocket can be opened before the BackgroundTask is dispatched
— `ProgressStore` ensures no events are lost to the race.
