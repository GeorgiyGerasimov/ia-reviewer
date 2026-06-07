# HTTP & WebSocket API

Base URL in the examples: `http://localhost:8000`.

## `GET /` — UI

The production UI is a **React SPA** built from `web/` and served by
the dedicated nginx container in `docker-compose.yml`. nginx terminates
`/` and reverse-proxies `/health`, `/review`, `/reviews/*`, `/reports/*`,
`/chat/*`, `/img.png`, and `/ws/*` to the `app` service. The legacy
Jinja template at `templates/index.html` is retained as a debug
fallback for direct `uvicorn` runs; it is not reachable through the
compose stack. See [ui.md](ui.md) for the frontend behaviour.

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

Pass a subset of `["dependency", "injection", "owasp", "configuration"]`
to skip reviewers. Empty list (default) runs all four.

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

## `GET /reviews/active` — in-flight reviews

Returns reviews currently running (registered via
`ActiveReviewsRegistry`). Pure in-memory snapshot; ordered by
`started_at` ascending so the longest-running run is first.

```bash
curl -s http://localhost:8000/reviews/active
# [
#   {"thread_id": "...", "mode": "repo",
#    "target": "https://github.com/owner/repo",
#    "ref": "main",
#    "started_at": 12345.6, "elapsed_s": 42},
#   ...
# ]
```

Backs the UI's "Active reviews" panel (poll every 5 s). Empty list
when no reviews are running.

## `POST /reviews/{thread_id}/cancel` — stop an in-flight review

Cooperatively cancels the underlying `asyncio.Task`. The review
coroutine catches `CancelledError`, broadcasts
"Review cancelled by user." into the chat, runs `finally:` cleanup
(snapshot rm, registry unregister), and exits without persisting a
partial state to the DB.

```bash
curl -s -X POST http://localhost:8000/reviews/<thread_id>/cancel
# 200 → {"status": "cancelled", "thread_id": "..."}
# 404 → {"detail": "no cancellable review"} when:
#   - the thread_id is unknown
#   - the task hasn't been attached yet (sub-millisecond race window)
#   - the task already finished
```

Backs the UI's per-row Stop button. The button confirms before
sending (`confirm("Stop this review?")`), and refreshes the active
list immediately on response so the row disappears as soon as the
registry drops the entry.

## `GET /reviews/{thread_id}/critical-findings` — critical findings + exploit status

Powers the UI's "Critical findings" panel. Returns one row per
`severity=critical` finding with a content-addressed `finding_id` and
the current `exploit_status` (or `null` if no exploit was attempted).

```bash
curl -s http://localhost:8000/reviews/<thread_id>/critical-findings
# [
#   {
#     "finding_id": "abc123def456",   # stable 12-char hash of role|file|line|issue
#     "role": "injection",
#     "severity": "critical",
#     "file": "src/auth.py",
#     "line": 42,
#     "issue": "SQL injection via login form",
#     "exploit_status": null,         # or "approved" / "skipped_low_confidence"
#     "confidence": null              # set when an attempt was made
#   }
# ]
```

Status codes:
- 200 — review found; rows returned (empty array when no critical findings)
- 404 — review unknown, or `app.state.review_store` is None

Sorted by `(role, file, line)` for stable UI ordering.

## `POST /reviews/{thread_id}/exploits/{finding_id}` — generate exploit PoC

Backs the per-row "Create exploit" button. Runs
`ExploitProposalAgent.generate_exploit(finding)` for ONE qualifying
critical finding — LLM draft (with confidence self-rating) followed by
LLM artifact generation when confidence ≥ 5/10. Persists the resulting
`ExploitProposal` to the `reviews.exploit_proposals` JSONB column and
writes a sibling `reports/<thread_id>.exploit.<finding_id>.md` file
when the artifact is produced.

```bash
curl -s -X POST http://localhost:8000/reviews/<thread_id>/exploits/<finding_id>
```

Status codes:
- 201 — generated (approved with artifact, or `skipped_low_confidence`)
- 200 — already created (idempotent return of existing record)
- 400 — finding exists but is not `critical` severity
- 404 — review or finding_id unknown, or `app.state.review_store` is None
- 409 — `MAX_EXPLOIT_PROPOSALS=3` reached for this review
- 503 — `app.state.exploit_agent` not configured (misconfigured deploy)

Response body (201 / 200):

```json
{
  "finding_id": "abc123def456",
  "role": "injection",
  "severity": "critical",
  "status": "approved",
  "proposal_text": "Attacker submits crafted payload …",
  "artifact": "curl -X POST http://localhost:8000/login --data …",
  "confidence": 8
}
```

Defensive use only — the LLM prompts and rendered sibling files all
carry the canonical disclaimer (see [`CLAUDE.md`](../CLAUDE.md#defensive-use-only--strict-policy)).
The artifact is hard-instructed to target only the developer's own
local environment; if the finding implies a third-party live system,
the model emits the literal string `REFUSED: third-party target`.

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
