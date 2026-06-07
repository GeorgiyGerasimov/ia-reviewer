# UI

The production UI is a **React 18 + TypeScript + Vite + shadcn/ui +
Tailwind** SPA in [`web/`](../web/), built into a static bundle and
served by a dedicated nginx container in `docker-compose.yml`. nginx
terminates `/` and reverse-proxies `/health`, `/review`, `/reviews/*`,
`/reports/*`, `/chat/*`, `/img.png`, and `/ws/*` to the `app` service.

The legacy Jinja template at `templates/index.html` is retained only
as a debug fallback for direct `uvicorn` runs — it's not reachable
through the compose stack and not the source of truth for UI behavior.

## Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  🐴  ia-reviewer                          [☀ Light]  View traces ↗│
│      Multi-agent security review for GitHub …                    │
├───────────────┬──────────────────────────────────────────────────┤
│ WORKFLOW      │ Trigger a review                                 │
│  ● Download   │  [https://github.com/owner/repo  or .../pull/N ] │
│  ● Validate   │  [ref optional]                  [ Review ]      │
│    tree       │                                                  │
│  ◐ Security   │ Critical findings (N)                            │
│    reviewers  │  ⚠ Defensive use only — …                        │
│    ● Dep.     │  ┌────────────────────────────────────────────┐  │
│    ● Inj.     │  │ injection · critical · src/app.py:42       │  │
│    ● OWASP    │  │ SQLi via f-string             [Create PoC] │  │
│    ● Config.  │  ├────────────────────────────────────────────┤  │
│  ◯ Review     │  │ owasp · critical · src/admin.py:12         │  │
│    decision   │  │ Missing auth on admin route   [Create PoC] │  │
│  ◯ Aggregate  │  └────────────────────────────────────────────┘  │
│  ◯ Publish    │  (scrollable, ~3 rows visible, rest scroll)      │
│  ◯ Rejected   │                                                  │
├───────────────┤ Token usage                                      │
│ PER-FILE SCAN │  12.4k  (10.8k in · 1.6k out · 24 calls)         │
│  Injection    │  ▸ Per-node breakdown                            │
│  ████░░░ 12/54│                                                  │
│  skipped 104  │ Final report                       [view raw .md]│
│  test         │  ## Security review                              │
│  OWASP 8/56   │  Summary                                         │
│  Config 6/6   │  ┌─────────┬─────┬─────┬──────┬─────┬─────┐      │
│  (done)       │  │Severity │ Dep │ Inj │ OWASP│Conf │Total│      │
├───────────────┤  │Critical │  0  │  2  │  1   │  1  │  4  │      │
│ ACTIVE        │  └─────────┴─────┴─────┴──────┴─────┴─────┘      │
│ REVIEWS  1    │                                                  │
│ ● repo  35s   │                                                  │
│   owner/repo  │                                                  │
│        [Stop] │                                                  │
├───────────────┤                                                  │
│ PAST REVIEWS  │                                                  │
│  critical 47  │                                                  │
│  major    3   │                                                  │
└───────────────┴──────────────────────────────────────────────────┘
```

Sidebar (left) has four panels:

- **Workflow** ([`web/src/components/WorkflowDiagram.tsx`](../web/src/components/WorkflowDiagram.tsx)) —
  vertical list of graph nodes, each with a status dot that changes
  colour as the review progresses. A synthetic `Security reviewers`
  parent groups the four child specialists (Dependency / Injection /
  OWASP / **Configuration** — split out of OWASP for A05 misconfig +
  A07 default-creds + secret exposure). In **PR mode** the `Download`
  row is pre-painted grey since `clone_repo` only fires for repo
  reviews — the topology stays visible but operators see at a glance
  that the step doesn't apply.
- **Per-file scan** ([`web/src/components/FileProgressPanel.tsx`](../web/src/components/FileProgressPanel.tsx)) —
  visible in repo-mode. One progress bar per reviewer
  (`Injection 12/54`), plus a `skipped 12 test · 4 docs (out of scope)`
  line per role (purpose-based file filter — see
  [`src/scanners/file_classifier.py`](../src/scanners/file_classifier.py)).
  Auto-hidden in PR mode (no per-file iteration).
- **Active reviews** ([`web/src/components/ActiveReviewsList.tsx`](../web/src/components/ActiveReviewsList.tsx)) —
  in-flight runs across all browser tabs (polled every 5 s from
  `GET /reviews/active`). Each row has elapsed time and a red **Stop**
  button that calls `POST /reviews/{id}/cancel`. Clicking the row
  switches the main pane to that thread.
- **Past reviews** ([`web/src/components/PastReviewsList.tsx`](../web/src/components/PastReviewsList.tsx)) —
  most recent persisted runs from the DB, severity-coloured dot per
  row + finding count.

Right pane (top-to-bottom): trigger form, Critical findings panel,
Token usage, Final report. The Critical findings panel is bounded
(`max-h-[288px]`, overflow-y-auto) so ~2-3 rows show by default and
operators can scroll within the panel without pushing the Final report
off-screen.

### Header pills

- **Theme toggle** ([`web/src/components/ThemeToggle.tsx`](../web/src/components/ThemeToggle.tsx)) —
  flips `<html data-theme="dark">`, persisted via `localStorage` key
  `ia-reviewer-theme`. A boot script in `web/index.html` reads the
  same key BEFORE stylesheet parsing so reloads in dark mode don't
  flash light.
- **View traces ↗** — link to Langfuse when `/health` reports a
  non-null `langfuse_url`. Wired via
  [`useHealth.ts`](../web/src/lib/useHealth.ts).

## URL auto-detection

The form ([`web/src/components/ReviewForm.tsx`](../web/src/components/ReviewForm.tsx))
routes to PR or repo mode based on a regex check for `/pull/N` in
the URL. A URL like `https://github.com/owner/repo/tree/develop`
goes to repo-mode; the backend's `normalize_repo_url` strips the
`/tree/develop` segment and extracts `develop` as the ref so the
UI's `ref` field is optional when the URL already carries one.

## Status colours

Each workflow circle has five possible states. They map to the
backend's progress envelope `status` field plus a client-derived
`active` pulsing animation cascaded by
[`useReviewStream.ts`](../web/src/lib/useReviewStream.ts)'s reducer.

| State | data-status | Colour | When |
|---|---|---|---|
| Pending | `pending` | transparent outline | Default before any event. |
| Active | `active` | blue, pulsing (`animate-pulse`) | Backend emitted `status:"active"` for this node, **or** the hook cascaded `active` from the predecessor in `CASCADE_ACTIVE`. Indicates work in progress. |
| Fired | `fired` | green | `status:"fired"`. Node ran with real output. |
| Empty | `empty` | dark gray | `status:"empty"` (scope-filtered reviewer returned `{}`), or set by `coerceTerminal` on `__done__` / `__cancelled__` for any node that never fired, **or** pre-set for `clone_repo` when mode is `pr`. |
| Rejected | `rejected` | red | `status:"rejected"` — `notify_rejection` actually ran. Only happens when the validator rejected the request. |

Priority across multiple events for the same node:
`fired > rejected > empty`. Terminal states are sticky — a late
`active` cascade can't downgrade them.

## Branch-aware coloring

`validate_request` is the accept/reject branch point. Its progress
envelope carries an extra `accepted: bool` field. The reducer's
`ACCEPT_FANOUT` immediately resolves the mutually-exclusive successors:

- `accepted: true` → cascade `active` to the four specialists +
  the synthetic `security_reviewers` parent. `notify_rejection`
  stays pending (the WorkflowDiagram dims it via
  `validationAccepted` + `branchState`).
- `accepted: false` → reviewers / decision / aggregate / publish
  stay pending and get dimmed; only `notify_rejection` continues.

Without this hint the UI would spin _both_ branches for several
seconds until the actual next event arrived.

## Active cascade

[`useReviewStream.ts::CASCADE_ACTIVE`](../web/src/lib/useReviewStream.ts) maps
each "upstream completed" event to the successor(s) that should
pulse blue:

```ts
const CASCADE_ACTIVE: Record<string, readonly string[]> = {
  clone_repo: ["validate_request"],
  dependency_review: ["review_decision"],
  injection_review: ["review_decision"],
  owasp_review: ["review_decision"],
  configuration_review: ["review_decision"],
  review_decision: ["aggregate_results"],
  aggregate_results: ["publish_report"],
};
```

When a node moves to fired/empty/rejected, the hook marks each
listed successor `active` UNLESS it's already in a terminal state.
The branch point (`validate_request`) is intentionally NOT in this
map — it uses `ACCEPT_FANOUT` instead.

## Race-proof workflow events

The backend's BackgroundTask starts immediately after the 202; the
browser's WebSocket handshake is slower. Without storage the earliest
progress events would be lost. `ProgressStore` solves this:
`_stream_graph_with_progress` writes every envelope into the store
**before** broadcasting, and the WS handler replays everything from
the store on connect (in order, before any new live events). The
terminal `{node:"__done__"}` is stored too, so a tab that opens after
the review finishes still sees the full final state.

## Report panel with retry

[`useReview.ts`](../web/src/lib/useReview.ts) and
[`useCriticalFindings.ts`](../web/src/lib/useCriticalFindings.ts) both
retry on 404 with exponential backoff: `300 → 600 → 1200 → 2400 →
4800 ms` (≈9s total). This handles the race between WS `__done__`
and `_persist_review` writing the DB row — the BackgroundTask
broadcasts terminal BEFORE the persistence step lands. After the
schedule is exhausted, the panels show empty until the next refresh.

When the parent threadId changes (operator switches between
reviews), both hooks synchronously clear their state so a past
review's data doesn't briefly show against an in-progress thread.

## Markdown rendering

[`web/src/lib/markdown.ts`](../web/src/lib/markdown.ts) implements an
inline parser (~80 lines of TypeScript) that handles headings `#`–`####`,
bullets, **bold**, _italic_, `` `inline code` ``, ```` ```fenced``` ````
blocks, GFM tables, and `[text](url)` / bare `/reports/...md` links.
Every user-controlled value is escaped first (`escapeHtml`) so the
`dangerouslySetInnerHTML` on the report panel is XSS-safe by
construction.

## Review button state

While a review is running the button is disabled and reads "Reviewing…".
Re-enabled by the WS terminal envelope (`__done__` / `__cancelled__`)
or by a 4xx/5xx / network error on the initial POST.

## Where to look when something's wrong

Open browser DevTools → Network → WS frame inspector. You'll see
every progress envelope arriving live. If the UI is stuck:

- **All circles stay idle** — `_run_repo_review` either errored at
  clone, or the WS isn't connecting. Check `docker compose logs -f
  app web` for the server side.
- **`Validate tree` stays active for too long** — backend's
  `validate_request` is taking long (or hung). Should be instant for
  repo-mode (pure code), 1–3s for PR-mode (LLM judge).
- **A reviewer stays active for ages** — LLM gateway is slow. Watch
  `docker compose logs -f app` for `graph node <name> done` lines.
- **`Publish` stays grey, Final report says "Report not available"** —
  graph reached publish_report but file write was skipped. Check
  `docker compose logs app | grep "publish:"` for
  `skipping disk write — state.thread_id is empty` or
  `publish wrote report: …` (which means it did write but the
  retry budget ran out before the file appeared — file a bug).
