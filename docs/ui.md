# UI

A minimal single-page UI lives at [`templates/index.html`](../templates/index.html)
and is served by `GET /`. Goals: zero build step (no React, no
bundler), readable in 30 seconds.

## Layout

```
┌─────────────────────────────────────────────────────────────────┐
│  🦓  ia-reviewer                                                │
│      Multi-agent security review for GitHub …                   │
├──────────────┬──────────────────────────────────────────────────┤
│ WORKFLOW     │ Trigger a review                                 │
│  ○ Download  │  [https://github.com/owner/repo  or .../pull/N ] │
│  ○ Validate  │  [ref optional]               [ Review ]         │
│    tree      │  Review started …                                │
│  ○ Security  │                                                  │
│    reviewers │ Chat — thread <uuid>                             │
│    ○ Dep.    │  [agent] Review complete. Full report: /…md      │
│    ○ Inj.    │  …                                               │
│    ○ OWASP   │  [Type a message…]            [ Send ]           │
│  ○ Review    │                                                  │
│    decision  │ Final report                       [view raw .md]│
│  ○ Aggregate │  ## Security review                              │
│  ○ Exploit   │  **Repository:** …                               │
│    proposals │  …                                               │
│  ○ Publish   │                                                  │
│  ○ Rejected  │                                                  │
└──────────────┴──────────────────────────────────────────────────┘
```

Three columns of real content:

- **Left (workflow)** — vertical list of graph nodes, each with a
  status dot that changes colour as the review progresses.
- **Right top (form)** — single text field for any GitHub URL
  (auto-detected as PR or repo) + optional `ref` for repo-mode.
- **Right middle (chat)** — WebSocket-backed conversation tied to the
  current `thread_id`. Replays history on connect.
- **Right bottom (report)** — rendered Markdown of the final report.
  Hidden until the review reaches publish.

## URL auto-detection

JS reads the `target_url` field and routes to PR or repo mode based on
a regex:

```js
const isPr = /\/pull\/\d+/.test(url);
const payload = isPr ? {pr_url: url} : {repo_url: url, ref: ref || "HEAD"};
```

A URL like `https://github.com/owner/repo/tree/develop` goes to
repo-mode. The backend's `normalize_repo_url` strips the `/tree/develop`
path and extracts `develop` as the ref (so the UI's `ref` field is
optional when the URL already carries one).

## Status colours

Each workflow circle has four possible states. They map 1:1 to the
backend's progress envelope `status` field (see
[`api.md`](api.md#ws-wschatthread_id--live-chat--progress)) plus a
client-derived "active" pulsing animation.

| State | Class | Colour | When |
|---|---|---|---|
| Idle | _(none)_ | transparent outline | Default before any event. |
| Active | `.active` | blue, pulsing | Backend emitted `status:"active"` for this node, **or** the JS cascaded `.active` from the predecessor in `NEXT_AFTER`. Indicates work in progress. |
| Done | `.done` | green | `status:"fired"`. Node ran with real output. |
| Skipped | `.skipped` | dark gray | `status:"empty"` (e.g. scope-filtered reviewer returned `{}`, or `process_proposal` had no findings to draft), or set by `finalizeWorkflow` on `__done__` for any node that never fired at all. |
| Rejected | `.rejected` | red | `status:"rejected"` — `notify_rejection` actually ran. Only happens when the validator rejected the request. |

Priority across multiple events for the same node:
`done > rejected > skipped`. So a later `fired` upgrades an earlier
`empty`, and a single `rejected` is sticky.

## Branch-aware coloring

`validate_request` is the accept/reject branch point. Its progress
envelope carries an extra `accepted: bool` field. The JS handler
`handleValidationResult(accepted)` immediately resolves the
mutually-exclusive successors:

- `accepted: true` → mark `notify_rejection` as `.skipped` (it will
  never fire). Cascade `.active` to the security-reviewer fan-out.
- `accepted: false` → mark all the happy-path nodes (`dependency`,
  `injection`, `owasp`, `security_reviewers`, `review_decision`,
  `aggregate_results`, `process_proposal`, `publish_report`) as
  `.skipped`. Cascade `.active` to `notify_rejection`.

Without this hint the UI would spin _both_ branches for several
seconds until the actual next event arrived, which looked confusing.

## Active cascade

Most non-branch nodes use a static `NEXT_AFTER` map to derive
"who's-next" when a node completes:

```js
const NEXT_AFTER = {
    clone_repo: ["validate_request"],
    dependency_review: ["review_decision"],
    injection_review: ["review_decision"],
    owasp_review: ["review_decision"],
    security_reviewers: ["review_decision"],
    review_decision: ["aggregate_results"],
    aggregate_results: ["process_proposal"],
    process_proposal: ["publish_report"],
};
```

When a node moves to `.done`/`.skipped`/`.rejected`, the JS spins the
successor by adding `.active`. The exact "who's next" for
`validate_request` is omitted on purpose — that's the branch point.

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

When the JS sees a `publish_report` or `notify_rejection` progress
event (or the terminal `__done__`), it calls `fetchAndRenderReport`.
This isn't a single fetch — it retries on 404 with exponential
backoff: `300 → 600 → 1200 → 2400 → 4800 ms` (≈9s total). A
sustained 404 (or any non-404 error) flips `reportFetchedForThread`
so future calls short-circuit and the panel shows the failure reason.

This handles the small race between "publish wrote the file" and "the
filesystem actually returns it from a subsequent `is_file()`".

## Markdown rendering

A tiny inline parser (≈40 lines of JS) handles headings `#`–`####`,
bullets, **bold**, _italic_, `` `inline code` ``, ```` ```fenced``` ````
blocks, and `[text](url)` / bare `/reports/...md` links. No external
library, no security surface, and good enough for our report format.

## Review button state

While a review is running the button is disabled and reads "Reviewing…".
Re-enabled by `finalizeWorkflow` (on `__done__`) or by a 4xx/5xx /
network error on the initial POST. The disabled state is *only* in
the form — chat input remains usable so the human can answer
interrupts.

## Where to look when something's wrong

Open browser DevTools → Network → WS frame inspector. You'll see every
progress envelope arriving live. If the UI is stuck:

- **All circles stay idle** — `_run_repo_review` either errored at
  clone, or the WS isn't connecting. Check the network tab + server
  log.
- **`Validate tree` stays active for too long** — backend's LangGraph
  validate_request is taking long (or hung). Should be instant for
  repo-mode (pure code), 1–3s for PR-mode (LLM judge).
- **A reviewer stays active for ages** — LLM gateway is slow. Watch
  the server log `graph node <name> done` lines to confirm.
- **`Publish` stays gray, Final report says "Report not available"** —
  graph reached publish_report but file write was skipped. Check the
  server log for `publish: skipping disk write — state.thread_id is
  empty` or a `publish wrote report: …` (which means it did write but
  the JS retry budget ran out before the file appeared — file a bug).
