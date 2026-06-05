# ia-reviewer — Multi-Agent Code Review System for GitHub

## Project overview

LangGraph-based multi-agent **security** code review system. Operates in two modes:

- **PR mode** — three specialist reviewers run in parallel against a GitHub Pull Request diff and publish a single consolidated comment on the PR.
- **Repo mode** — same three specialists scan a full snapshot of a repository at a given ref. Each reviewer iterates **per-file** over its own whitelist of paths (one LLM call per file) and produces a Markdown report saved locally as `reports/<thread_id>.md`; a brief summary with the report link is broadcast into the chat.

Three specialists:

- **Dependency** — manifest/lockfile diffs, CVEs, typosquats, supply-chain risk
- **Injection** — SQLi, command, template, deserialization, path traversal, XSS
- **OWASP Top 10** — broader sweep covering A01/A02/A04/A05/A07/A08/A09/A10 (A03 and A06 are delegated to the specialists above)

This project is modeled after `ms-ia-bot` (GitLab + Slack), but targets **GitHub only**, has **no Slack integration**, and is **security-focused** — there is no architecture / mobile / web / backend specialization.

Key files:
- `src/graph/state.py` — `ReviewState`, `ReviewRequest` (discriminated by `mode: "pr" | "repo"`), `RepoFile`, `AgentReview` dataclasses. `agent_reviews` uses an `add` reducer so the three security agents can write concurrently. `ReviewState.thread_id` is stamped before invocation so repo-mode publish can write `reports/<thread_id>.md`.
- `src/graph/coordinator.py` — `build_review_graph(coordinator, dependency, injection, owasp, *, checkpointer=None)` — fan-out from `START` to the three reviewers, join at `aggregate_results`, then `publish_report → END`
- `src/agents/base_reviewer.py` — `BaseReviewer` (shared machinery: scope filter, path-match, `_run_pr`, `_parse_response`) plus two sibling base classes that pick a repo-mode strategy: `LLMPerFileReviewer` (one LLM call per matched file — used by Injection / OWASP) and `ScriptedScannerReviewer` (subclass owns repo-mode entirely — used by Dependency). Subclassing `BaseReviewer` directly without overriding `_run_repo` raises `NotImplementedError` so the omission is loud.
- `src/agents/report_renderer.py` — pure-render module (no I/O). `ReportRenderer.render_review`, `render_rejection`, `render_exploit_sibling`, `exploit_artifact_filename`. `CoordinatorAgent` delegates here; tests assert on Markdown without spinning up a graph or a tempdir.
- `src/agents/validator.py` — `RequestValidator`: pure-code prefilter (empty / docs-only / autogen / oversized) + LLM judge for ambiguous PRs. Writes `ValidationVerdict` to `state.validation`. Default-accept on LLM parse failure.
- `src/agents/{dependency,injection,owasp}.py` — three specialist reviewers. `DependencyReviewer` in repo-mode is **script-first**: it delegates to `DependencyScanner` (deterministic OSV.dev lookup) and only calls the LLM once for a summary paragraph — see "Script-first dependency scan" below.
- `src/scanners/dependency_scanner.py` — `DependencyScanner.scan(repo_files, snapshot_root) -> ScanResult`. Parses every supported manifest (`package-lock.json`, `requirements.txt`, …) into `Dep(name, ecosystem, version)`, batches them into a single `OSVClient.query` call, maps every returned `Vuln` to a finding dict with CVE id, CVSS-derived severity bucket, fixed versions, and references. `ScanResult.unsupported_files` records recognised-but-unparsed manifests (e.g. `go.sum`, `Cargo.lock`) so the reviewer can disclose partial coverage.
- `src/integrations/manifests/{npm,pip}.py` — manifest parsers feeding the scanner. npm covers lockfile v1/v2/v3; pip covers strictly-pinned `==`/`===` lines in `requirements*.txt` (PEP 503 names, `[extras]` stripped, environment markers stripped, unpinned ranges skipped).
- `src/integrations/osv_client.py` — async client for OSV.dev `/v1/querybatch` + `/v1/vulns/{id}`. Dedups advisories by id, normalises CVSS into a single severity string, surfaces aliases (CVE ids).
- `src/agents/review_decision.py` — `ReviewDecisionAgent`: detects ambiguity, calls `interrupt()` to pause for the human, parses the resume payload, optionally re-routes back to the reviewers with `extra_context`.
- `src/agents/exploit_proposal.py` — `ExploitProposalAgent`: per-finding confidence self-check, draft, human-gated `interrupt()`, artifact generation on approval. Stable content-addressed `finding_id` so chat resume can target it (`approve <id>` / `decline <id>`).
- `src/agents/coordinator.py` — I/O-only graph nodes. `aggregate()` asks `ReportRenderer` for the body and stores it in `state.final_report`; `publish()` writes to disk + optionally posts a PR comment; `finalize_exploits()` re-renders after the exploit loop and writes sibling artifact files; `notify_rejection()` posts the category-templated rejection. **No Markdown generation lives here any more** — that's all `ReportRenderer`.
- `src/integrations/github.py` — GitHub REST client. PR-mode only: `fetch_pr` + `post_pr_comment`. Repo-mode does NOT use this client.
- `src/integrations/repo_fetcher.py` — repo-mode cloning. `clone_repo(repo_url, ref) -> Path` (shallow `git clone --depth=1 --branch=<ref> --single-branch` into a unique `tempfile.mkdtemp(prefix="ia-review-")` dir; injects `settings.GITHUB_TOKEN` into the HTTPS URL when set for private repos) + `list_repo_files(snapshot_dir) -> list[RepoFile]` (walks the tree, skips `.git/`, filters by `MAX_FILE_BYTES=200_000`) + `cleanup_snapshot(path)`. Replaces the previous REST-tree-+-blob-per-file approach which routinely hit GitHub's 60 req/h anonymous rate limit.
- `src/models/factory.py` — `ModelFactory` with AI Gateway + direct provider modes
- `src/utils/config.py` — pydantic-settings config loader
- `src/utils/checkpointer.py` — `open_checkpointer(database_url)` async context manager wrapping `AsyncPostgresSaver` lifecycle
- `src/utils/tracing.py` — `get_langfuse_callback()` returns a Langfuse `CallbackHandler` or `None`. Threaded into every `graph.ainvoke` config by `main._trace_config`.
- `src/chat/{store,hub}.py` — in-memory `ChatStore` (history per `thread_id`) and `ChatHub` (broadcast over live `WebSocket` connections). Future: move chat into LangGraph's `messages` channel when human-in-the-loop interrupts land.
- `templates/index.html` — minimal HTML UI: PR-URL form + chat panel. Vanilla JS posts to `/review`, opens a `WebSocket` to the returned `thread_id`.
- `main.py` — FastAPI app. Lifespan opens the checkpointer and compiles the graph; tests use `create_test_app(graph=..., github=..., store=..., hub=...)` to bypass lifespan with injected mocks.
- `docker-compose.yml` — `app` + `postgres` (pgvector/pgvector:pg16)
- `db/init/*.sql` — runs once on first Postgres boot; enables the `vector` extension

## Persistence

A single Postgres instance backs **both** the LangGraph checkpointer (run state, message history) and the pgvector vectorstore (embeddings for reference materials and past reviews). Connection comes from `DATABASE_URL`.

- Use `localhost` in `.env` when running the app from `.venv` against the compose-managed db
- docker-compose overrides `DATABASE_URL` to use the in-network `postgres` hostname
- `db/init/01-extensions.sql` enables `CREATE EXTENSION vector` on first boot only — subsequent schema migrations should live elsewhere

## Project skills

Detailed conventions live as project skills under [`.claude/skills/`](.claude/skills/). The CLAUDE.md summaries below are the TL;DR — refer to the corresponding SKILL.md for the full rules:

- [`tdd-workflow`](.claude/skills/tdd-workflow/SKILL.md) — TDD/XP red→green→refactor cycle + 10-iteration hard stop
- [`testing-principles`](.claude/skills/testing-principles/SKILL.md) — mocking strategy, test layering, fixtures
- [`venv-policy`](.claude/skills/venv-policy/SKILL.md) — dependencies live in `.venv/`, never system Python
- [`docs-in-refactor`](.claude/skills/docs-in-refactor/SKILL.md) — README/docs are part of every refactor

## Development workflow: TDD (XP)

**Always write tests before implementation.** For every new feature or change:

1. Write a failing test that describes the desired behaviour
2. Verify it fails (`pytest -x`) — confirms the test is actually testing something
3. Write the minimal implementation to make it pass
4. Verify it passes
5. Refactor if needed, keeping tests green

**Hard stop:** If the red→green cycle exceeds 10 iterations without the test passing — stop. Report what the test expects, what the code produces, and why it's stuck. Ask the user for a decision: redesign the test, redesign the implementation, or skip.

## Testing principles

### Never make real API calls in tests
All external clients must be mocked at the boundary:
- `httpx` → mock with `respx`
- LLM models → patch `ModelFactory.get` to return `AsyncMock` with controlled `.content`

### Fixtures live in `tests/fixtures/` as real files
`.diff` and `.json` files reflect real GitHub API responses. Load them via `conftest.py` helpers. Do not inline large strings in test code.

### Test layers
- **Unit** — pure logic, zero I/O: routing functions, URL parsers, block builders, `_parse_response`
- **Integration** — one component, all dependencies mocked: `agent.run()`, `client.fetch_pr()`
- **E2E** — full LangGraph graph run, all external I/O mocked

## Graph topology

```
START → validate_request
            │ conditional
            ├──→ notify_rejection ──→ END                          (reject)
            └──→ [dependency_review, injection_review, owasp_review]   (accept, parallel)
                       ↓ fan-in
                review_decision
                       ↓ conditional
                       ├──→ [reviewers]   (rerun on human-approved clarification, cycle_count++)
                       └──→ aggregate_results
                                ↓
                         publish_report               (report durable BEFORE any Q&A)
                                ↓
                         process_proposal             (Phase C — chat-only, doesn't update file)
                                ↓ conditional
                                ├──→ process_proposal (more pending findings)
                                └──→ END
```

**Phase A** inserted `validate_request` between START and the security-reviewer fan-out. `RequestValidator` runs a pure-code prefilter (empty diff / docs-only / autogen / oversized) and falls through to an LLM judge for anything ambiguous (trolling vs legitimate). The verdict is a `ValidationVerdict(accepted, category, reason)` stored in `state.validation`. The conditional edge `route_after_validation`:
- `accepted=True` → fan out to all three reviewers in parallel
- `accepted=False` or `validation is None` (safety net) → `notify_rejection`

`notify_rejection` posts a short category-templated PR comment via `CoordinatorAgent.notify_rejection` and ends the run. Templates live in `src/agents/coordinator.py::_REJECTION_TEMPLATES` keyed by category — tests assert on phrases derived from the category, not the prose.

**Phase B** inserted `review_decision` between the reviewer fan-in and `aggregate_results`. It runs an ambiguity heuristic (`_needs_clarification`: reviewer signaled a problem with `passed=False` but produced no concrete findings) and, on ambiguous output, calls LangGraph's `interrupt()` to pause the graph. `main.py._run_review` then broadcasts the question into the chat panel (via `ChatStore` + `ChatHub`). The WebSocket handler detects pending interrupts via `graph.aget_state(...).tasks`, routes the user's next message as `Command(resume=text)`, and lets the node resume.

Resume parsing: messages starting with `rerun` (case-insensitive) approve a re-review; everything after `rerun:` (or `rerun `) becomes `state.extra_context` which `BaseReviewer._build_context` splices into the next pass. Bounded by `MAX_CYCLES=3` — initial pass + up to 2 human-approved reruns, then `review_decision` force-routes to `aggregate_results` regardless of ambiguity.

`agent_reviews` keeps the `add` reducer (parallel-safety) but accumulates one entry per role per pass; `CoordinatorAgent._render_report` deduplicates by role keeping the latest pass.

**Phase C** inserted `process_proposal` between `aggregate_results` and `publish_report`. **Only `critical` findings qualify** — the LLM-cost-budget for PoC generation is reserved for the worst-case impact validation, and `major`/`minor`/`info` findings keep surfacing in the main report but never trigger an interactive cycle. The qualifying set is pinned by `_QUALIFYING_SEVERITIES = frozenset({"critical"})` in `src/agents/exploit_proposal.py`; tests in `tests/unit/test_exploits_critical_only.py` guard against accidental re-addition of lower severities.

For each qualifying critical finding, `ExploitProposalAgent`:

1. LLM self-rates **confidence** (0–10) for crafting a reliable PoC and drafts a `proposal_text`.
2. Below the confidence threshold (5/10) → status `skipped_low_confidence`, **no human prompt**.
3. Otherwise → `interrupt({kind: exploit_approval, finding_id, role, proposal, question})`. The chat WS surfaces this; `main.py` schedules an `EXPLOIT_TIMEOUT_SECONDS=60` task that resumes with `decline <finding_id>` if the human doesn't reply (status `timeout_declined`). The user replies `approve <finding_id>` or `decline <finding_id>`.
4. On `approve` → a second LLM call generates the **artifact** (PoC code / repro steps); status `approved`, artifact populated.
5. Conditional edge: more findings → loop back to `process_proposal`; otherwise → `publish_report`.

**Loop cap.** Bounded by `MAX_EXPLOIT_PROPOSALS=3`. Once 3 findings have been processed (any combination of approve/decline/skip/timeout), every remaining pending finding is batch-skipped in one invocation with status `skipped_cap_reached` — no LLM call, no human prompt. Prevents runaway iteration on PRs with many critical findings.

**Cycle counter in the UI.** Every `exploit_approval` `interrupt()` payload includes `cycles_used` (decisions already landed, 0-indexed) and `cycles_max` (`MAX_EXPLOIT_PROPOSALS`). `main._broadcast_pending_interrupts` copies them into the chat `interrupt` block, and the UI button-chip renders an "Exploit cycle N of M (K decisions left after this one)" line above the Approve / Decline buttons. The same counter appears in the LLM-visible question text so it shows up in Langfuse traces too. Decisions in this counter count regardless of outcome — approve, decline, low-confidence-skip, and timeout-decline all advance it.

**Sequential, not parallel** — one question in the chat at a time. The `add` reducer on `state.exploit_proposals` is kept for future migration to `Send`-fanout if we ever decide latency matters more than UX.

**Defensive use only — strict policy.** The exploit branch exists to let a security engineer validate the **real** impact of a finding **against their own code**, in an isolated environment. It is NOT a tool for attacking third-party systems or for any destructive purpose. The canonical disclaimer lives in `src/agents/exploit_proposal.py::EXPLOIT_DISCLAIMER` and is surfaced everywhere a PoC is exposed:

  - **Chat prompt** — first line of the `interrupt()` question, BEFORE the human clicks Approve.
  - **UI button row** — visible amber notice strip rendered above the Approve / Decline buttons.
  - **LLM prompts** — both `_DRAFT_PROMPT` and `_ARTIFACT_PROMPT` carry the text. `_ARTIFACT_PROMPT` adds hard rules instructing the model to: (a) target only local reproduction (developer's own machine), (b) **refuse outright** with the string `REFUSED: third-party target` when the finding implies a third-party live system, (c) never include real credentials / tokens / keys, (d) use placeholders like `localhost:8000` and clearly-fake values.
  - **Rendered report** — `## Exploit proposals` section in the main `<thread_id>.md` opens with a blockquote-formatted disclaimer.
  - **Sibling files** — every `save_mode="file"` artifact (`<thread_id>.exploit.<finding_id>.md`) repeats the disclaimer at the top.

The `publish_report` markdown gains an "Exploit proposals" section: approved entries get their proposal text plus (for `save_mode="embed"`) a `<details>`-collapsed artifact OR (for `save_mode="file"`) a link to a sibling `<thread_id>.exploit.<finding_id>.md`. Declined / skipped / timeout entries appear compactly in a "Not generated" bullet list. The section is omitted entirely when `exploit_proposals` is empty.

All three security agents on the accept path still run concurrently. LangGraph waits for all of them at `review_decision` (fan-in). Each agent returns a partial update `{"agent_reviews": [one_review]}` which the `add` reducer concatenates into `state.agent_reviews`. Per-node un-reduced fields (e.g. `final_report`, `pr_comment_id`) are written only by `aggregate_results` / `publish_report` — never concurrently.

`ALLOWED_SCOPE_ROLES = ("dependency", "injection", "owasp")`. Pass `scope=["injection"]` on a `ReviewRequest` to run just one reviewer.

## Observability — Langfuse

LLM-level tracing is wired through [src/utils/tracing.py](src/utils/tracing.py). `get_langfuse_callback()` returns a `langfuse.langchain.CallbackHandler` when both `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set (and `LANGFUSE_HOST` if you're self-hosting), otherwise `None`. Missing keys, missing package, or init failure all silently disable tracing — the app never goes down because Langfuse is unreachable.

The handler is created once in the lifespan and stored on `app.state.langfuse_callback`. `_trace_config(app, thread_id, state, trigger)` composes the LangGraph `config` dict: `configurable.thread_id` (always), plus `callbacks=[handler]` and `metadata={session_id, user_id, pr_url, tags}` when tracing is enabled. Both initial review (`trigger="http"`) and resume-after-interrupt (`trigger="resume"`) go through the same helper, so every LLM call across the whole review thread groups under one Langfuse session keyed by `thread_id`.

**Local Langfuse via compose.** The project's `docker-compose.yml` ships the full Langfuse v3 self-hosted stack (`langfuse-web` + `langfuse-worker` + `clickhouse` + `redis` + `minio`) **as default-on services** — every `docker compose up` brings the trace UI up alongside the app:

```bash
docker compose up -d                           # app + postgres + langfuse + clickhouse + redis + minio
open http://localhost:3000                     # login: dev@local.dev / localdev123!
```

The UI's header shows a "View traces ↗" link when `/health` reports a `langfuse_url` — operators click through to the trace tree without remembering the URL. The URL itself comes from `LANGFUSE_HOST` (driven by `.env`), so the same link works for Langfuse Cloud setups too.

**Trace coverage.** Every LLM call across the graph is captured automatically — LangChain's contextvar-based callback propagation carries the `CallbackHandler` from `graph.astream(config=…)` down to every `model.ainvoke(prompt)` inside the nodes (`RequestValidator`, the three security reviewers, `ReviewDecisionAgent`, `ExploitProposalAgent`, `DependencyReviewer._summarise`). On top of that, `DependencyScanner.scan` and `OSVClient.query` are decorated with `@langfuse.observe(as_type="tool")`, so the deterministic OSV.dev path appears as named spans nested under the surrounding graph trace. With Langfuse off, both `@observe` and the absent CallbackHandler degrade to no-ops — the app never blocks on observability.

On first boot Langfuse self-seeds an organization, project, and user with the keys baked into `.env.example` (`pk-lf-dev` / `sk-lf-dev`). The app reads the same keys at startup, so tracing works immediately with zero manual configuration. Langfuse stores its transactional data in a separate `langfuse` database inside the existing `pgvector` Postgres (created on first cluster init by `db/init/02-create-langfuse-db.sh`); analytics events go to ClickHouse and event blobs to MinIO.

If you want to skip Langfuse on a constrained host, scope the up command: `docker compose up -d app postgres`. Or stop the stack after the fact: `docker compose stop langfuse-web langfuse-worker clickhouse redis minio`.

For Langfuse Cloud: set `LANGFUSE_HOST=https://cloud.langfuse.com` and paste real keys into `.env`; the local stack stays unused.

## Checkpointing

`build_review_graph(..., checkpointer=...)` forwards to `graph.compile()`. Production wires `AsyncPostgresSaver` via `open_checkpointer(settings.DATABASE_URL)` inside the FastAPI lifespan — `.setup()` runs on first start to create checkpoint tables. When `DATABASE_URL` is empty, `open_checkpointer` yields `None` and the graph runs without checkpointing. Tests pass `None` directly or `MemorySaver()` to exercise the resumable path.

Resumable runs require `config={"configurable": {"thread_id": ...}}` on `ainvoke`. The `/review` endpoint mints a UUID4 `thread_id`, returns it in the 202 response, and uses it as the checkpoint key.

## HTTP API

```bash
# UI (form + chat)
open http://localhost:8000/

# liveness
curl -s http://localhost:8000/health
# {"status": "ok"}

# trigger a PR review (returns immediately, runs in background)
curl -s -X POST http://localhost:8000/review \
  -H 'Content-Type: application/json' \
  -d '{"pr_url": "https://github.com/owner/repo/pull/42"}'
# {"status": "started", "thread_id": "<uuid>", "pr_url": "..."}

# trigger a whole-repo review at a ref (default ref = HEAD)
curl -s -X POST http://localhost:8000/review \
  -H 'Content-Type: application/json' \
  -d '{"repo_url": "https://github.com/owner/repo", "ref": "main"}'
# {"status": "started", "thread_id": "<uuid>", "repo_url": "...", "ref": "main"}

# only a subset of reviewers (works in both modes)
curl -s -X POST http://localhost:8000/review \
  -d '{"pr_url": "...", "scope": ["injection", "dependency"]}'

# fetch a saved repo-mode report
curl -s http://localhost:8000/reports/<thread_id>.md

# chat history for an existing thread
curl -s http://localhost:8000/chat/<thread_id>/history
# []  — or list of {role, text, timestamp}
```

Validation: exactly one of `pr_url` / `repo_url` is required (400 on neither or both). `scope` (if present) must be a subset of `ALLOWED_SCOPE_ROLES`. Successful requests return `202` and the review runs as a FastAPI `BackgroundTask` — exceptions inside the background run are logged, not surfaced.

## Repo-mode review

Trigger with `{"repo_url": "...", "ref": "main"}`. Flow:

1. `_run_repo_review` calls `repo_fetcher.clone_repo(repo_url, ref)` — a shallow `git clone --depth=1 --branch=<ref> --single-branch` into a unique tempdir (`tempfile.mkdtemp(prefix="ia-review-")`). For private repos `settings.GITHUB_TOKEN` is injected into the HTTPS URL as basic-auth. Then `list_repo_files(snapshot_dir)` walks the tree, skips `.git/`, drops files larger than `MAX_FILE_BYTES=200_000`. The snapshot path is stored in `state.request.snapshot_dir`; the file list in `state.request.repo_files`. **A single `git` subprocess replaces what used to be `1 + N` GitHub REST calls** — the 60 req/h anonymous rate limit no longer applies. `git` is a runtime dependency; if it's not on `PATH`, `clone_repo` raises a clean `RuntimeError`.
2. `validate_request` runs repo-mode pure-code rules (no LLM judge): empty tree → reject as `empty_repo`; size > `MAX_REPO_FILES_HARD=5000` → reject as `oversized_repo`; otherwise accept.
3. Each of the three reviewers filters `repo_files` through its own `PATH_PATTERNS` (fnmatch against basename) and caps the result at `settings.MAX_FILES_PER_AGENT=200`. File content is read directly from the local snapshot (`Path(state.request.snapshot_dir) / repo_file.path`) — no network round-trip per file. Findings are tagged with their file path. Truncation (cap exceeded) produces an explicit `truncated: N files over cap of M were not scanned` note in the AgentReview summary, which surfaces in the final report.
   - **`InjectionReviewer` and `OWASPTop10Reviewer`** run **one LLM call per file** — the model reads source and emits findings JSON.
   - **`DependencyReviewer` is script-first** (see "Script-first dependency scan" below) — it parses every matched manifest deterministically and queries OSV.dev exactly once. The LLM is invoked at most once at the end, only to write a plain-English summary on top of the scanner's findings. CVE ids, fixed versions, references, and severity buckets are derived from OSV.dev, never from the model.
4. The Phase B human-in-the-loop re-review path and Phase C exploit-proposal path work identically to PR mode.
5. `CoordinatorAgent.publish` branches on `state.request.mode`: in repo-mode it writes the rendered Markdown to `<reports_dir>/<thread_id>.md` and skips the GitHub comment entirely. `notify_rejection` writes the templated rejection notice to the same file.
6. `_run_repo_review` broadcasts a short `Review complete. Full report: /reports/<thread_id>.md` chip into the chat once the graph completes (skipped while interrupts are pending). The UI's `renderMessage` linkifies the `/reports/...md` path so the operator can click through.
7. **Snapshot lifecycle.** `_run_repo_review` removes the cloned tempdir in `finally:` (whether the graph completed cleanly or errored). On Phase B human-in-the-loop reruns the snapshot stays alive because the rerun happens within the same `astream` invocation; resume-after-interrupt does NOT re-clone (the file system reads are over by the time we'd pause in `review_decision` / `process_proposal`).

### Per-specialist path whitelists

- **DependencyReviewer.PATH_PATTERNS** — manifests and lockfiles only: `package.json`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `requirements*.txt`, `Pipfile*`, `poetry.lock`, `pyproject.toml`, `setup.py`, `go.mod`, `go.sum`, `Cargo.toml`, `Cargo.lock`, `Gemfile*`, `composer.json`, `composer.lock`, `pom.xml`, `build.gradle*`, `*.csproj`, `*.fsproj`, etc.
- **InjectionReviewer.PATH_PATTERNS** — source files only: `*.py`, `*.js`, `*.ts`, `*.tsx`, `*.go`, `*.java`, `*.kt`, `*.rb`, `*.php`, `*.cs`, `*.rs`, `*.c`, `*.cpp`, `*.sql`, `*.sh`, etc. Docs and config explicitly excluded.
- **OWASPTop10Reviewer.PATH_PATTERNS** — superset: same source files **plus** infra/config (`Dockerfile*`, `docker-compose*.yml`, `*.tf`, `*.yml`, `*.yaml`, `*.env*`, `nginx.conf`, etc.) so A05/A07/A08 checks see the actual deployment surface.

### Caps and config

- `MAX_FILE_BYTES = 200_000` (in `src/integrations/repo_fetcher.py`) — per-file size cap during snapshot walk; minified bundles and binary blobs are dropped.
- `settings.MAX_FILES_PER_AGENT = 200` — per-reviewer file iteration cap. Env-overridable.
- `settings.MAX_REPO_FILES_HARD = 5000` — total tree size hard cap; trees larger than this are rejected outright by the validator.
- `settings.REPORTS_DIR = "reports"` — directory where `<thread_id>.md` files are written (and where `GET /reports/{filename}` reads from).
- `settings.SNAPSHOTS_DIR = "snapshots"` — project-local root for `git clone --depth=1` snapshots. Each review gets a unique subdir (`tempfile.mkdtemp(prefix="ia-review-", dir=SNAPSHOTS_DIR)`), removed in `_run_repo_review`'s `finally:` once the graph completes. Project-local (rather than `$TMPDIR`) so the path is predictable under Docker and easy to bind-mount via `-v ./snapshots:/app/snapshots` for debugging.

### Script-first dependency scan

`DependencyReviewer._run_repo` does **not** loop the LLM over manifest files. Two consecutive scans of the same lockfile must produce byte-identical findings — the model can't deliver that on its own (it hallucinates CVE numbers and flips severities run-to-run), so the data path is now:

1. **Parse.** `DependencyScanner` walks `state.request.repo_files`, dispatches each matched basename through `_PARSERS` (currently `package-lock.json` → `parse_package_lock`, `requirements.txt` → `parse_requirements_txt`), and reads each manifest from `snapshot_root / repo_file.path`. Output: list of `Dep(name, ecosystem, version)`, deduped across manifests.
2. **Query OSV.** A single `OSVClient.query(deps)` call covers every parsed package across ecosystems (`npm`, `PyPI`, …). One batched HTTP round-trip per scan; mixed ecosystems share the same `/v1/querybatch` call. Each surfaced advisory id is then resolved via `/v1/vulns/{id}` (dedup'd globally).
3. **Render findings.** Every `(Dep, Vuln)` pair becomes a finding dict with `{file, package, version, ecosystem, advisory_id, cve, issue, severity, fixed, refs}`. Severity is derived from the OSV CVSS_V3 score:
   - `score ≥ 9.0` → `critical`
   - `score ≥ 7.0` → `major`
   - `score ≥ 4.0` → `minor`
   - missing/unparseable score → `minor` (a real advisory still surfaces)
   The order is stable: iterate `deps` in input order, then each dep's vulns in OSV's response order. The findings list is `==`-equal between runs given the same input.
4. **LLM summary (single call).** The reviewer feeds the findings list + disclosures into `_SUMMARY_PROMPT` and asks the model for 2-4 paragraphs of plain English. The model is explicitly told **not to invent CVE numbers** — only describe what's in the findings list. When there are zero findings the LLM is **skipped entirely** and a deterministic one-liner is composed inline.
5. **Partial-coverage disclosure.** `ScanResult.unsupported_files` collects manifests recognised by `DependencyReviewer.PATH_PATTERNS` but for which no scanner-side parser is wired (`go.sum`, `Cargo.lock`, `pom.xml`, `pyproject.toml`, …). `ScanResult.notes` collects parser errors (corrupt JSON, missing file) and OSV outage (network failure → empty findings + a note, graph keeps going). Both are folded into the summary so the report makes coverage honest.

PR-mode `DependencyReviewer` is unchanged — the diff is small enough that the single-LLM-call approach against `prompt_template` still works. Adding diff-aware OSV resolution there is future work.

**Currently parsed:** `package-lock.json` (npm v1/v2/v3 lockfiles), `requirements.txt` (strictly-pinned `==`/`===` entries, extras and env markers stripped, ranges silently skipped). **Recognised-but-unparsed** (surfaced as partial coverage): `yarn.lock`, `pnpm-lock.yaml`, `Pipfile.lock`, `poetry.lock`, `pyproject.toml`, `go.sum`, `Cargo.lock`, `Gemfile.lock`, `composer.lock`, `pom.xml`, `gradle.lockfile`, `*.csproj`.

## Chat WebSocket

`WS /ws/chat/{thread_id}` — duplex JSON chat tied to a review thread.

- **On connect:** the server replays the full history for `thread_id` from `ChatStore`.
- **Client → server:** `{"text": "..."}` is wrapped into a `ChatMessage(role="user", ...)`, stored, then broadcast.
- **Server → all clients on the thread:** `{"role", "text", "timestamp"}` JSON, including the original sender (so a single tab sees its own message confirmed).
- **Isolation:** broadcasts only reach sockets on the same `thread_id`.

Today the server only echoes user messages back to connected clients (it doesn't yet generate agent replies). The contract is in place so agents can later call `hub.broadcast(thread_id, {"role": "agent", "text": "..."})` when LangGraph's `interrupt()` raises a question for the human.

## Model assignments

Internal default per agent (the `model_name` argument to `BaseReviewer`):

| Agent | Internal name |
|-------|--------------|
| Validator (LLM judge) | `claude-sonnet-4-6` |
| Dependency | `claude-sonnet-4-6` |
| Injection | `claude-sonnet-4-6` |
| OWASP Top 10 | `claude-sonnet-4-6` |
| Exploit proposal | `claude-sonnet-4-6` |

The actual model that gets called depends on the gateway resolution (see below). Override per agent via the `model_name` constructor argument when instantiating.

## Model routing via the AI Gateway

The default `USE_AI_GATEWAY=true` routes every LLM call through an OpenAI-compatible endpoint at `AI_GATEWAY_URL` (default: `http://10.30.1.14:8001/v1`, the local model server on the corp LAN). The actual model name passed to that endpoint is resolved in [src/models/factory.py](src/models/factory.py)::`_resolve_gateway_model`:

1. **Explicit pin** — `settings.AI_GATEWAY_MODEL` if set (e.g. `Qwen/Qwen2.5-Coder-32B-Instruct`).
2. **Auto-discovery** — sync GET to `<AI_GATEWAY_URL>/models`, take the first `data[].id`. Cached in module-level `_discovered_gateway_model` so the network is hit at most once per process. 5-second timeout.
3. **Bifrost fallback** — `provider/internal-name` mapping (`anthropic/claude-sonnet-4-6`) if both above fail. Useful when the gateway is actually Bifrost / LiteLLM rather than a single-model box.

So the same agent code works against (a) a local vLLM/llama.cpp box (auto-discovery picks the served model), (b) a pinned local model (set `AI_GATEWAY_MODEL`), (c) a Bifrost-style multi-provider proxy (leave both empty, mapping fires), or (d) `USE_AI_GATEWAY=false` plus per-provider keys for direct Anthropic/OpenAI/Google calls.

Discover the model name on the default box:

```bash
curl -s http://10.30.1.14:8001/v1/models | jq '.data[].id'
```

## Dependencies and virtual environment

**All Python dependencies live in `.venv/` at the project root.** Never install into system Python.

First-time setup:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Any Python tool invocation must go through `.venv`:
- `.venv/bin/python ...`
- `.venv/bin/pytest ...`
- `.venv/bin/ruff ...`
- or `source .venv/bin/activate` once per shell

## Running tests

```bash
source .venv/bin/activate       # once per shell
pytest                          # all tests
pytest tests/unit               # unit only
pytest tests/integration        # integration only
pytest tests/e2e                # e2e only
pytest -x -q                    # stop on first failure
```
