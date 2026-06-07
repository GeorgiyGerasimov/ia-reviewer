# ia-reviewer — Multi-Agent Code Review System for GitHub

## Project overview

LangGraph-based multi-agent **security** code review system. Operates in two modes:

- **PR mode** — four specialist reviewers run in parallel against a GitHub Pull Request diff and publish a single consolidated comment on the PR.
- **Repo mode** — same four specialists scan a full snapshot of a repository at a given ref. Each reviewer iterates **per-file** over its own whitelist of paths (one LLM call per file) and produces a Markdown report saved locally as `reports/<thread_id>.md`; a brief summary with the report link is broadcast into the chat.

Four specialists, each with its own file whitelist + prompt:

- **Dependency** — manifest/lockfile diffs, CVEs, typosquats, supply-chain risk. Script-first via OSV.dev (deterministic); one LLM call only for the narrative summary.
- **Injection** — SQLi, command, template, deserialization, path traversal, XSS. Source code only.
- **OWASP Top 10** — application-level OWASP (A01 / A02 / A04 / A07-logic / A08 / A09 / A10). Source code only. A03 → Injection; A06 → Dependency; A05 misconfiguration + A07 default-credentials + secret exposure → **Configuration**.
- **Configuration** — fourth specialist split out of OWASP because misconfig / default-creds / secret findings were overwhelming the OWASP block in real reports. Scans `Dockerfile`, `docker-compose*.yml`, `.env*`, `*.tf`, `nginx.conf`, plus generic INI / TOML / YAML / properties. Prompt focused on misconfigurations, weak defaults, exposed secrets, container/IaC hardening, reverse-proxy headers. See `src/agents/configuration.py`.

Architecturally borrows the parallel-reviewers + coordinator pattern from a sibling code-review tool that targeted GitLab + Slack, but ia-reviewer targets **GitHub only**, has **no Slack integration**, and is **security-focused** — there is no architecture / mobile / web / backend specialization.

Key files:
- `src/graph/state.py` — `ReviewState`, `ReviewRequest` (discriminated by `mode: "pr" | "repo"`), `RepoFile`, `AgentReview` dataclasses. `agent_reviews` uses an `add` reducer so the three security agents can write concurrently. `ReviewState.thread_id` is stamped before invocation so repo-mode publish can write `reports/<thread_id>.md`.
- `src/graph/coordinator.py` — `build_review_graph(coordinator, dependency, injection, owasp, configuration, *, checkpointer=None)` — fan-out from `START` to the four reviewers, join at `review_decision` → `aggregate_results`, then `publish_report → END`
- `src/agents/base_reviewer.py` — `BaseReviewer` (shared machinery: scope filter, path-match, `_run_pr`, `_parse_response`) plus two sibling base classes that pick a repo-mode strategy: `LLMPerFileReviewer` (one LLM call per matched file — used by Injection / OWASP) and `ScriptedScannerReviewer` (subclass owns repo-mode entirely — used by Dependency). Subclassing `BaseReviewer` directly without overriding `_run_repo` raises `NotImplementedError` so the omission is loud.
- `src/agents/report_renderer.py` — pure-render module (no I/O). `ReportRenderer.render_review`, `render_rejection`, `render_exploit_sibling`, `exploit_artifact_filename`, `splice_tldr`. `CoordinatorAgent` delegates here; tests assert on Markdown without spinning up a graph or a tempdir. **Report layout** (deterministic, no LLM): per-role findings are sorted `critical → major → minor → info`; a `### Summary` table with severity × role × Total counts is emitted right after the `**Overall severity:**` header when any findings exist; a cross-cutting `### Critical findings (N)` callout precedes the per-role sections when `N > 0`. Both blocks are dropped when empty so non-critical reports stay tight. `splice_tldr(body, text)` inserts (or idempotently REPLACES) a `### TL;DR` block between the overall-severity header and the Summary table — used by `ReportFormatter`.
- `src/agents/report_formatter.py` — optional LLM-copywriter polish for the report. Runs as the `format_report` graph node between `aggregate_results` and `publish_report`. **Off by default** (`settings.ENABLE_REPORT_FORMATTER=False`): node short-circuits to `{}`, no LLM call. When ON and findings exist: builds a tight findings-only prompt (no free-form summaries leak in), asks the LLM for 2–3 paragraphs, splices via `ReportRenderer.splice_tldr`. Hard-rules in the prompt: do not invent files / CVE ids / severities; do not add findings; no JSON, no fences. Hard cap on response length (`REPORT_FORMATTER_MAX_CHARS=2000`). **Fail-soft on everything**: any exception, empty response, or oversized response → returns `{}`, deterministic report from aggregate stays the published version. **Optional inline LLMJudge gate** (`settings.FORMATTER_JUDGE_CHECK=True`): every generated TL;DR is graded by `LLMJudge` against a 4-criterion formatter rubric (`tldr_only_real_files`, `tldr_only_real_cves`, `tldr_respects_severity`, `tldr_no_invented_findings`) — the side-by-side eval doc gives the judge both the findings ground-truth and the proposed TL;DR. **Fail-CLOSED for the gate**: judge says `passed=False` OR judge call errors → TL;DR dropped, deterministic report wins. This is the "judge inline in the graph" production pattern of `docs/judge-in-production.md`, distinct from the offline CLI use of `src/evals/judge_report.py`.
- `src/agents/validator.py` — `RequestValidator`: pure-code prefilter (empty / docs-only / autogen / oversized) + LLM judge for ambiguous PRs. Writes `ValidationVerdict` to `state.validation`. Default-accept on LLM parse failure.
- `src/agents/{dependency,injection,owasp}.py` — three specialist reviewers. `DependencyReviewer` in repo-mode is **script-first**: it delegates to `DependencyScanner` (deterministic OSV.dev lookup) and only calls the LLM once for a summary paragraph — see "Script-first dependency scan" below.
- `src/scanners/dependency_scanner.py` — `DependencyScanner.scan(repo_files, snapshot_root) -> ScanResult`. Parses every supported manifest (`package-lock.json`, `requirements.txt`, …) into `Dep(name, ecosystem, version)`, batches them into a single `OSVClient.query` call, maps every returned `Vuln` to a finding dict with CVE id, CVSS-derived severity bucket, fixed versions, and references. `ScanResult.unsupported_files` records recognised-but-unparsed manifests (e.g. `go.sum`, `Cargo.lock`) so the reviewer can disclose partial coverage.
- `src/integrations/manifests/{npm,pip}.py` — manifest parsers feeding the scanner. npm covers lockfile v1/v2/v3; pip covers strictly-pinned `==`/`===` lines in `requirements*.txt` (PEP 503 names, `[extras]` stripped, environment markers stripped, unpinned ranges skipped).
- `src/integrations/osv_client.py` — async client for OSV.dev `/v1/querybatch` + `/v1/vulns/{id}`. Dedups advisories by id, normalises CVSS into a single severity string, surfaces aliases (CVE ids).
- `src/agents/review_decision.py` — `ReviewDecisionAgent`: detects ambiguity, calls `interrupt()` to pause for the human, parses the resume payload, optionally re-routes back to the reviewers with `extra_context`.
- `src/agents/exploit_proposal.py` — `ExploitProposalAgent`: on-demand exploit PoC generation. `generate_exploit(finding)` runs draft (LLM rates confidence 0–10) + artifact (LLM produces concrete PoC). No graph wiring, no interrupts — invoked **directly** by the `POST /reviews/{tid}/exploits/{fid}` endpoint when the operator clicks "Create exploit" in the UI. Stable content-addressed `finding_id` (`compute_finding_id`) used by the endpoint to address findings. Critical-only severity policy.
- `src/agents/coordinator.py` — I/O-only graph nodes. `aggregate()` asks `ReportRenderer` for the body and stores it in `state.final_report`; `publish()` writes to disk + optionally posts a PR comment; `notify_rejection()` posts the category-templated rejection. **No Markdown generation lives here any more** — that's all `ReportRenderer`.
- `src/integrations/github.py` — GitHub REST client. PR-mode only: `fetch_pr` + `post_pr_comment`. Repo-mode does NOT use this client.
- `src/integrations/repo_fetcher.py` — repo-mode cloning. `clone_repo(repo_url, ref) -> Path` (shallow `git clone --depth=1 --branch=<ref> --single-branch` into a unique `tempfile.mkdtemp(prefix="ia-review-")` dir; injects `settings.GITHUB_TOKEN` into the HTTPS URL when set for private repos) + `list_repo_files(snapshot_dir) -> list[RepoFile]` (walks the tree, skips `.git/`, filters by `MAX_FILE_BYTES=200_000`) + `cleanup_snapshot(path)`. Replaces the previous REST-tree-+-blob-per-file approach which routinely hit GitHub's 60 req/h anonymous rate limit.
- `src/models/factory.py` — `ModelFactory` with AI Gateway + direct provider modes
- `src/utils/config.py` — pydantic-settings config loader
- `src/utils/checkpointer.py` — `open_checkpointer(database_url)` async context manager wrapping `AsyncPostgresSaver` lifecycle
- `src/utils/tracing.py` — `get_langfuse_callback()` returns a Langfuse `CallbackHandler` or `None`. Threaded into every `graph.ainvoke` config by `main._trace_config`.
- `src/chat/{store,hub}.py` — in-memory `ChatStore` (history per `thread_id`) and `ChatHub` (broadcast over live `WebSocket` connections). Future: move chat into LangGraph's `messages` channel when human-in-the-loop interrupts land.
- `src/chat/active_reviews.py` — `ActiveReviewsRegistry` tracking in-flight reviews. `register(thread_id, mode, target, ref)` / `unregister(thread_id)` are called by `_run_review` / `_run_repo_review` around `graph.ainvoke`. `GET /reviews/active` returns a snapshot for the UI's "Active reviews" poll (5s interval; opens chosen thread in a new tab via `?thread=<id>` which auto-attaches the WS and replays from `ProgressStore`).
- `src/chat/progress_emitter.py` + `src/chat/progress_log.py` — per-file progress envelopes for repo-mode reviews. `ProgressEmitter` lives in a `contextvar` set by `_run_repo_review` before `graph.ainvoke`; `LLMPerFileReviewer._run_repo` calls `await maybe_emit(...)` on `started_batch` / `file_done` (per file, with index/total/findings_count/failed flag) / `finished`. Same emitter writes to `ProgressStore` (replay-on-connect) AND broadcasts via `ChatHub` (live UI). `progress_log_loop` is a background `asyncio.Task` spawned per review that emits one condensed `progress_snapshot thread_id=<id> elapsed_s=<int> dependency=<done>/<total> injection=<done>/<total>(<current>) owasp=...` line on the `graph.progress` logger every `interval_seconds` (default 60s). Cancelled in the review's `finally:` block with a final snapshot flushed before exit.
- `templates/index.html` — minimal HTML UI: PR-URL form + chat panel. Vanilla JS posts to `/review`, opens a `WebSocket` to the returned `thread_id`.
- `main.py` — FastAPI app. Lifespan opens the checkpointer and compiles the graph; tests use `create_test_app(graph=..., github=..., store=..., hub=...)` to bypass lifespan with injected mocks.
- `docker-compose.yml` — `app` + `postgres` (pgvector/pgvector:pg16)
- `db/init/*.sql` — runs once on first Postgres boot; enables the `vector` extension

## RAG — retrieval over past findings

A `retrieve_past_context` node fans into the three reviewers from `validate_request`'s accept branch. It embeds a query derived from the incoming `ReviewRequest` (`pr_url + author + files_changed` in PR-mode; `repo_url + ref + repo_files` in repo-mode) via [`src/integrations/embedder.py`](src/integrations/embedder.py), then calls `ReviewStore.retrieve_similar(target_url, role, query_embedding, k=RAG_TOP_K)` once per role in `state.request.scope` (or all three roles when scope is empty). The result lands in `state.past_findings_by_role: dict[str, list[dict]]` (no reducer — single writer, last write wins).

Each reviewer's `_build_context` / `_build_repo_file_context` reads its own role slice from `state.past_findings_by_role` and renders a "Previous findings on this repo" block into the LLM prompt — one bullet per past finding with severity + file location + issue text. Different roles never see each other's slices: `InjectionReviewer` never sees `dependency`'s past findings.

**Storage.** Embeddings live in `review_findings.embedding vector(EMBEDDING_DIM)` with an `ivfflat (vector_cosine_ops)` index. The schema migration in `db/init/03-reviews-schema.sql` is replayed idempotently by `ReviewStore.ensure_schema()` at app startup. **Changing `EMBEDDING_DIM` after the first run is a destructive op** — the column must be dropped + recreated + all rows backfilled. Pin it.

**Backfill.** After every `save_review(state)`, `main._persist_review` calls `store.embed_pending(embedder)` in the same async task. It scans for `review_findings.embedding IS NULL`, embeds each row's `role — file — category — issue` text, and UPDATEs the vector in. Stops on the first embedder failure so a misbehaving gateway doesn't burn through the backlog without writing anything useful.

**Fail-soft contract.** RAG never blocks a review. Every degraded state below collapses to "no past context spliced this run":
- `EMBEDDING_MODEL=""` → no embedder built in lifespan; `PastContextAgent.run` short-circuits.
- `DATABASE_URL=""` or `ReviewStore.create` failed → `store=None`; same short-circuit.
- Gateway has no `/embeddings` endpoint, network error, dim mismatch, malformed JSON → `Embedder.embed` returns `None`; agent skips retrieval.
- Per-role `retrieve_similar` raises → caught and converted to an empty list for that role only; other roles still get their slice.

**Config.** Five env vars in [`.env.example`](.env.example):
- `EMBEDDING_MODEL` — master switch (empty = disabled).
- `EMBEDDING_DIM` — must match the model (default 1024 for BGE-large / multilingual-e5-large).
- `EMBEDDING_API_URL` / `EMBEDDING_API_KEY` — optional override; empty = reuse `AI_GATEWAY_URL` / `AI_GATEWAY_API_KEY`.
- `RAG_TOP_K` — past-findings cap per reviewer (default 5).

The status line `RAG enabled: model=... dim=N top_k=K` (or one of the three "RAG disabled (...)") is logged at lifespan start so operators see at a glance whether retrieval is live.

## Persistence

A single Postgres instance backs **both** the LangGraph checkpointer (run state, message history) and the pgvector vectorstore (embeddings for reference materials and past reviews). Connection comes from `DATABASE_URL`.

- Use `localhost` in `.env` when running the app from `.venv` against the compose-managed db
- docker-compose overrides `DATABASE_URL` to use the in-network `postgres` hostname
- `db/init/01-extensions.sql` enables `CREATE EXTENSION vector` on first boot only — subsequent schema migrations should live elsewhere

## Security posture

OWASP LLM Top 10 (2025) self-assessment + project baseline lives in [`docs/security-checklist.md`](docs/security-checklist.md), summarised as a table in [`README.md::Security checklist`](README.md#security-checklist). When adding a new capability, walk that file: a change to side effects re-evaluates LLM06; a new external dependency re-evaluates LLM03; a new format property of the review re-evaluates LLM09 and may want a new `benchmarks/judge` case so quality regressions surface quantitatively.

Out of scope (documented gaps): per-user authentication, rate limiting, immutable audit log, HTTPS termination, browser CSP. These all move in-scope the moment the app is exposed outside the corp network.

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
            └──→ retrieve_past_context                              (accept — RAG step)
                       ↓
                [dependency_review, injection_review, owasp_review, configuration_review]   (parallel)
                       ↓ fan-in
                review_decision
                       ↓ conditional
                       ├──→ [reviewers]   (rerun on human-approved clarification, cycle_count++)
                       └──→ aggregate_results
                                ↓
                         format_report                (PR #3 — optional LLM TL;DR; no-op when flag off)
                                ↓
                         publish_report → END
```

After `publish_report`, exploit-PoC generation happens **out of band** via
the on-demand `POST /reviews/{tid}/exploits/{fid}` endpoint — the UI's
"Critical findings" panel lists qualifying findings and the user clicks
"Create exploit" per row. See "On-demand exploit generation" below.

**Phase A** inserted `validate_request` between START and the security-reviewer fan-out. `RequestValidator` runs a pure-code prefilter (empty diff / docs-only / autogen / oversized) and falls through to an LLM judge for anything ambiguous (trolling vs legitimate). The verdict is a `ValidationVerdict(accepted, category, reason)` stored in `state.validation`. The conditional edge `route_after_validation`:
- `accepted=True` → `retrieve_past_context` (the RAG step now sits between the validator and the reviewers; from there, a static edge fans out to all three reviewers in parallel)
- `accepted=False` or `validation is None` (safety net) → `notify_rejection`

`notify_rejection` posts a short category-templated PR comment via `CoordinatorAgent.notify_rejection` and ends the run. Templates live in `src/agents/coordinator.py::_REJECTION_TEMPLATES` keyed by category — tests assert on phrases derived from the category, not the prose.

**Phase B** inserted `review_decision` between the reviewer fan-in and `aggregate_results`. It runs an ambiguity heuristic (`_needs_clarification`: reviewer signaled a problem with `passed=False` but produced no concrete findings) and, on ambiguous output, calls LangGraph's `interrupt()` to pause the graph. `main.py._run_review` then broadcasts the question into the chat panel (via `ChatStore` + `ChatHub`). The WebSocket handler detects pending interrupts via `graph.aget_state(...).tasks`, routes the user's next message as `Command(resume=text)`, and lets the node resume.

Resume parsing: messages starting with `rerun` (case-insensitive) approve a re-review; everything after `rerun:` (or `rerun `) becomes `state.extra_context` which `BaseReviewer._build_context` splices into the next pass. Bounded by `MAX_CYCLES=3` — initial pass + up to 2 human-approved reruns, then `review_decision` force-routes to `aggregate_results` regardless of ambiguity.

`agent_reviews` keeps the `add` reducer (parallel-safety) but accumulates one entry per role per pass; `CoordinatorAgent._render_report` deduplicates by role keeping the latest pass.

### On-demand exploit generation

After the security review publishes the report, qualifying **critical** findings can be turned into PoCs interactively. **Only `critical` findings qualify** — the LLM-cost-budget for PoC generation is reserved for worst-case impact validation, and `major`/`minor`/`info` findings keep surfacing in the main report but never get an exploit button. The qualifying set is pinned by `_QUALIFYING_SEVERITIES = frozenset({"critical"})` in `src/agents/exploit_proposal.py`; tests in `tests/unit/test_exploits_critical_only.py` guard against accidental re-addition of lower severities.

**UX flow** (replaces the previous in-graph `process_proposal` interrupt loop, which the user described as "перегружен и совершенно не функционален"):

1. Report publishes → UI's "Critical findings" panel renders one compact row per critical finding (role / severity / file:line / brief issue).
2. One-line defensive-use disclaimer sits at the top of the panel (single source of truth for everyone, no per-finding repetition).
3. Per-row button is state-dependent:
   - none → `[ Create exploit ]` → `POST /reviews/{tid}/exploits/{fid}`
   - approved → `[ View existing ]` → opens sibling `/reports/{tid}.exploit.{fid}.md` in a new tab
   - declined / skipped_low_confidence → grey status badge, no button
   - cap reached → button disabled with "Cap reached" label
4. Endpoint pipeline for a single click:
   1. Idempotency check — same `finding_id` already processed? Return existing record (200).
   2. Cap check — `MAX_EXPLOIT_PROPOSALS=3` already attempted? Return 409.
   3. `ExploitProposalAgent.generate_exploit(finding)` — LLM rates confidence + drafts a proposal text. Below 5/10 → status `skipped_low_confidence`, no artifact, persisted.
   4. Otherwise → second LLM call generates the artifact (concrete PoC code / repro steps). Status `approved`.
   5. Persist via `ReviewStore.add_exploit_proposal` (JSONB append). Write sibling `<tid>.exploit.<fid>.md` file. Return the proposal (201).

**Cap.** `MAX_EXPLOIT_PROPOSALS=3` records per review (any combination of approved / skipped). Beyond the cap the endpoint returns 409. Prevents unbounded LLM spend on noisy reviews.

**Defensive use only — strict policy.** The exploit capability exists to let a security engineer validate the **real** impact of a finding **against their own code**, in an isolated environment. It is NOT a tool for attacking third-party systems or for any destructive purpose. The canonical disclaimer lives in `src/agents/exploit_proposal.py::EXPLOIT_DISCLAIMER` and is surfaced everywhere a PoC is exposed:

  - **UI panel** — one-line amber notice strip above the per-row buttons in the Critical findings panel.
  - **LLM prompts** — both `_DRAFT_PROMPT` and `_ARTIFACT_PROMPT` carry the text. `_ARTIFACT_PROMPT` adds hard rules instructing the model to: (a) target only local reproduction (developer's own machine), (b) **refuse outright** with the string `REFUSED: third-party target` when the finding implies a third-party live system, (c) never include real credentials / tokens / keys, (d) use placeholders like `localhost:8000` and clearly-fake values.
  - **Sibling files** — every `<thread_id>.exploit.<finding_id>.md` repeats the disclaimer at the top.

Sibling artifact files live next to the main review report at `reports/<thread_id>.exploit.<finding_id>.md`. The UI's "View existing" link opens them directly.

All three security agents on the accept path still run concurrently. LangGraph waits for all of them at `review_decision` (fan-in). Each agent returns a partial update `{"agent_reviews": [one_review]}` which the `add` reducer concatenates into `state.agent_reviews`. Per-node un-reduced fields (e.g. `final_report`, `pr_comment_id`) are written only by `aggregate_results` / `publish_report` — never concurrently.

`ALLOWED_SCOPE_ROLES = ("dependency", "injection", "owasp")`. Pass `scope=["injection"]` on a `ReviewRequest` to run just one reviewer.

## Graph-node timing

Every node in `build_review_graph` is wrapped with [`timed_node(...)`](src/utils/node_timing.py) which logs one structured line per invocation on the dedicated `graph.timing` logger:

    node_complete node=<name> thread_id=<id> duration_ms=<int> status=ok | status=error error=<repr>

Field order is stable — downstream parsers (`grep node_complete | awk -F'duration_ms='`, jq pipelines, plotting scripts) depend on it. Wrapper measures `time.perf_counter()` (monotonic), never mutates `state`, re-raises on exception after logging.

Coverage: all 10 nodes (`validate_request`, `retrieve_past_context`, `notify_rejection`, four `*_review`, `review_decision`, `aggregate_results`, `format_report`, `publish_report`). `tests/integration/test_graph_timing_logged.py` catches any future `add_node` call that forgets the wrapper.

Cost + latency expectations per mode, plus the awk/grep recipes for p50/p95 extraction, live in [`docs/performance-and-cost.md`](docs/performance-and-cost.md). These are companion data to Langfuse: Langfuse covers LLM-call-level details (tokens, prompts), the structured log covers graph-orchestration-level details (which nodes ran, in what order, how long each took).

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
3. Each of the three reviewers filters `repo_files` in TWO stages: (a) `PATH_PATTERNS` (fnmatch against basename) — extension/name whitelist, (b) `SKIP_CATEGORIES` — purpose-based filter using `src/scanners/file_classifier.py::classify_file` (CORE / TEST / DOCS / INFRA / VENDORED / GENERATED). Then capped at `settings.MAX_FILES_PER_AGENT=200`. Defaults: `InjectionReviewer` skips TEST/DOCS/INFRA/VENDORED/GENERATED (only CORE source). `OWASPTop10Reviewer` skips TEST/DOCS/VENDORED/GENERATED (keeps CORE + INFRA — that's the A05/A07/A08 surface). Defaults can be overridden per-role via env (`INJECTION_SKIP_CATEGORIES`, `OWASP_SKIP_CATEGORIES`) as a CSV — unknown values crash at instance construction with a clear error. The `started_batch` envelope carries the `skipped: {category: count}` breakdown so the UI renders "skipped 12 tests · 4 docs (out of scope)" under each role's progress bar. File content is read directly from the local snapshot (`Path(state.request.snapshot_dir) / repo_file.path`) — no network round-trip per file. Per-file LLM calls within a reviewer are **fanned out concurrently** under `settings.MAX_CONCURRENT_FILES_PER_AGENT=5` (semaphore + `asyncio.gather(..., return_exceptions=True)`); since each call sees only one file's content, parallelism costs nothing on quality and cuts wall-clock linearly until the gateway saturates. Findings are tagged with their file path and aggregated in input order (stable across runs, independent of LLM completion order). Truncation (cap exceeded) produces an explicit `truncated: N files over cap of M were not scanned` note in the AgentReview summary, which surfaces in the final report.
   - **`InjectionReviewer` and `OWASPTop10Reviewer`** run **one LLM call per file** — the model reads source and emits findings JSON.
   - **`DependencyReviewer` is script-first** (see "Script-first dependency scan" below) — it parses every matched manifest deterministically and queries OSV.dev exactly once. The LLM is invoked at most once at the end, only to write a plain-English summary on top of the scanner's findings. CVE ids, fixed versions, references, and severity buckets are derived from OSV.dev, never from the model.
4. The Phase B human-in-the-loop re-review path works identically to PR mode. Exploit-PoC generation happens out of band via the `POST /reviews/{tid}/exploits/{fid}` endpoint after the report is published — see "On-demand exploit generation" above.
5. `CoordinatorAgent.publish` branches on `state.request.mode`: in repo-mode it writes the rendered Markdown to `<reports_dir>/<thread_id>.md` and skips the GitHub comment entirely. `notify_rejection` writes the templated rejection notice to the same file.
6. `_run_repo_review` broadcasts a short `Review complete. Full report: /reports/<thread_id>.md` chip into the chat once the graph completes (skipped while interrupts are pending). The UI's `renderMessage` linkifies the `/reports/...md` path so the operator can click through.
7. **Snapshot lifecycle.** `_run_repo_review` removes the cloned tempdir in `finally:` (whether the graph completed cleanly or errored). On Phase B human-in-the-loop reruns the snapshot stays alive because the rerun happens within the same `astream` invocation; resume-after-interrupt does NOT re-clone. The exploit endpoint doesn't need the snapshot — it works off the persisted `review_findings` DB rows.

### Per-specialist path whitelists

- **DependencyReviewer.PATH_PATTERNS** — manifests and lockfiles only: `package.json`, `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `requirements*.txt`, `Pipfile*`, `poetry.lock`, `pyproject.toml`, `setup.py`, `go.mod`, `go.sum`, `Cargo.toml`, `Cargo.lock`, `Gemfile*`, `composer.json`, `composer.lock`, `pom.xml`, `build.gradle*`, `*.csproj`, `*.fsproj`, etc.
- **InjectionReviewer.PATH_PATTERNS** — source files only: `*.py`, `*.js`, `*.ts`, `*.tsx`, `*.go`, `*.java`, `*.kt`, `*.rb`, `*.php`, `*.cs`, `*.rs`, `*.c`, `*.cpp`, `*.sql`, `*.sh`, etc. Docs and config explicitly excluded.
- **OWASPTop10Reviewer.PATH_PATTERNS** — superset: same source files **plus** infra/config (`Dockerfile*`, `docker-compose*.yml`, `*.tf`, `*.yml`, `*.yaml`, `*.env*`, `nginx.conf`, etc.) so A05/A07/A08 checks see the actual deployment surface.

### Caps and config

- `MAX_FILE_BYTES = 200_000` (in `src/integrations/repo_fetcher.py`) — per-file size cap during snapshot walk; minified bundles and binary blobs are dropped.
- `settings.MAX_FILES_PER_AGENT = 200` — per-reviewer file iteration cap. Env-overridable.
- `settings.MAX_CONCURRENT_FILES_PER_AGENT = 5` — per-reviewer concurrent LLM-call fan-out. `=1` collapses to sequential (back-compat baseline); 3-5 fits most hosted gateways; 8-16 for beefier self-host. Cap to ≤ gateway's effective max-concurrent — higher just queues at the server.
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

The default `USE_AI_GATEWAY=true` routes every LLM call through an OpenAI-compatible endpoint at `AI_GATEWAY_URL` (default: `http://localhost:8001/v1` — a local serving box; real deployments point at a Bifrost / LiteLLM proxy, corp-LAN gateway, or a public provider via the gitignored `.env` override). The actual model name passed to that endpoint is resolved in [src/models/factory.py](src/models/factory.py)::`_resolve_gateway_model`:

1. **Explicit pin** — `settings.AI_GATEWAY_MODEL` if set (e.g. `Qwen/Qwen2.5-Coder-32B-Instruct`).
2. **Auto-discovery** — sync GET to `<AI_GATEWAY_URL>/models`, take the first `data[].id`. Cached in module-level `_discovered_gateway_model` so the network is hit at most once per process. 5-second timeout.
3. **Bifrost fallback** — `provider/internal-name` mapping (`anthropic/claude-sonnet-4-6`) if both above fail. Useful when the gateway is actually Bifrost / LiteLLM rather than a single-model box.

So the same agent code works against (a) a local vLLM/llama.cpp box (auto-discovery picks the served model), (b) a pinned local model (set `AI_GATEWAY_MODEL`), (c) a Bifrost-style multi-provider proxy (leave both empty, mapping fires), or (d) `USE_AI_GATEWAY=false` plus per-provider keys for direct Anthropic/OpenAI/Google calls.

Discover the model name on whatever gateway you've pointed at:

```bash
curl -s "$AI_GATEWAY_URL/models" | jq '.data[].id'
```

## Frontend (web/)

The browser UI is a React subproject at `web/`. Full conventions live in [`web/README.md`](web/README.md); the TL;DR:

- **Stack.** React 18 + TypeScript (strict) + Vite + Vitest + React Testing Library + ESLint (flat) + Prettier.
- **Layout.** `web/src/{api, components, lib, styles, test}`. Tests sit next to their source as `*.test.{ts,tsx}` (e.g. `format.ts` + `format.test.ts`). Cross-component wiring tests can live under `web/tests/`.
- **Engineering rules mirror the backend.** TDD red→green→refactor. No real network in tests (mock `fetch`, fake socket class). No `any`. No new deps without justification. Run `npm run lint && npm run typecheck && npm test && npm run build` before committing.
- **Production = two images, zero source-coupling.**
  - `Dockerfile` (backend) — `py-builder` + `runtime`. Python only; ships NO frontend assets. `GET /` serves the legacy Jinja template as a debug fallback for direct `uvicorn main:app` runs. In compose this branch is never reached because nginx terminates `/`.
  - `web/Dockerfile` (frontend) — `node:22-alpine` builds the Vite SPA, then `nginx:alpine` serves it. The same nginx reverse-proxies `/health`, `/review`, `/reviews/*`, `/reports/*`, `/chat/*`, `/img.png`, and `/ws/*` to the `app` service. See `web/nginx.conf` — that file is the only place the two containers know about each other.
  - **No shared volumes, no COPY between stages, no `web/dist` in the backend image.** Either side can be redeployed independently or moved to a different host (SPA to a CDN, backend behind a separate LB) without code changes.
  - Compose maps host port `${IA_PORT:-8000}:80` to the `web` service; the backend is `expose:`-only (internal). `docker compose up --build` is the single command to launch the new UI.
- **CI.** `.github/workflows/ci.yml` has a dedicated `web` job that runs lint / typecheck / Vitest / Vite build. Independent of the Python job — a frontend regression surfaces even when pytest is clean.

The legacy `templates/index.html` is being migrated panel-by-panel. The status table in `web/README.md` tracks what's already React-native vs still served from Jinja.

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

## Benchmarks

`benchmarks/` (separate from `tests/`) measures agent accuracy across a fixed corpus of input/expected pairs and reports a single success-rate per suite. Three suites today implement the three-modality eval recipe (programmatic asserts + tool-call benchmarks + LLM-as-judge):

- **`validator`** (14 cases) — exercises `RequestValidator` across all seven categories. Mix of pure-code prefilter cases (empty diff, docs-only, autogenerated, oversized) and LLM-judge cases (accepted refactor / SQL fix / new endpoint + trolling). Run with `python -m benchmarks.run validator` (live LLM) or `--mock-llm` for hermetic CI runs.
- **`dependency`** (12 cases) — exercises `DependencyScanner` against OSV mocked inline per case. Covers npm v1/v2 lockfiles, pip requirements (plain pins, strict `===`, `[extras]`, env markers), mixed ecosystems, partial coverage (`Cargo.lock` recognised but unparsed), corrupt JSON, and severity edge cases. Always 100% deterministic.
- **`judge`** (8 calibration cases) — exercises `LLMJudge` from [`src/evals/llm_judge.py`](src/evals/llm_judge.py) against labelled "good" and "bad" review reports. The judge reads a finished report and grades it against a fixed rubric (overall severity present, all three roles mentioned, no fake CVE format, exploit disclaimer present, no raw JSON in body). 3 deliberately-good + 5 deliberately-broken calibration reports. Judge accuracy on this calibration set IS the suite's success_rate — regression alarm when the judge starts grading inconsistently. **Calibrates the offline judge surface** (`python -m src.evals.judge_report`).
- **`judge-formatter`** (8 calibration cases) — exercises `LLMJudge` against the formatter-rubric used by the `FORMATTER_JUDGE_CHECK` inline gate (see [`src/agents/report_formatter.py`](src/agents/report_formatter.py)). Fixtures are **side-by-side eval docs** (`## Findings (ground truth)` + `## Proposed TL;DR (under review)`), not full review reports. Rubric is formatter-specific: `tldr_only_real_files`, `tldr_only_real_cves`, `tldr_respects_severity`, `tldr_no_invented_findings`. 3 good (grounded TL;DR) + 5 bad (one per criterion failing in isolation + 1 multi-fail). **Calibrates the inline-gate judge surface** (the production graph node). Same `run_judge_benchmark` runner, different `cases.json`.

CLI: `python -m benchmarks.run [validator | dependency | judge | judge-formatter | all] [--mock-llm] [--min-success-rate FLOAT]`. Exit code 0 when every suite ≥ `--min-success-rate` (default 0.80), else 1 — wire into CI as a regression backstop.

`tests/integration/test_benchmark_runner.py` runs all four suites with `--mock-llm` as a plumbing sanity check (loader, runner, assertion shape) — full `pytest` covers it.

Current numbers (Sonnet 4.6, locally):
- `dependency`: **100% (12/12)** — deterministic
- `validator --mock-llm`: **86% (12/14)** — pure-code prefilter coverage; the two missed cases are trolling-detection that requires the LLM judge
- `judge --mock-llm`: **38% (3/8)** — constant-accept mock catches the 3 good reports by accident; the 62 pp gap to live judge is the value-add of LLM-as-judge over a naïve oracle
- `judge-formatter --mock-llm`: **38% (3/8)** — same mock-baseline as `judge`, on a completely different rubric. The 62 pp gap maps directly to the value-add of the inline-gate judge over "always-accept the formatter's output".

The mock-vs-live gap on `validator`, `judge`, and `judge-formatter` **is the meter** for how much each LLM-driven component contributes on top of deterministic rules / naïve oracles. Neither judge suite is CI-gated (mock-mode misses bad calibration cases by design).

## Running tests

```bash
source .venv/bin/activate       # once per shell
pytest                          # default — unit + integration + non-playwright e2e
pytest tests/unit               # unit only
pytest tests/integration        # integration only
pytest tests/e2e                # e2e only (still skips playwright/)
pytest -x -q                    # stop on first failure

# Playwright suite (synchronous browser API; runs its own loop).
# Must clear the project default addopts that excludes it, otherwise
# the directory is silently skipped:
pytest tests/e2e/playwright/ --override-ini='addopts='
```

The default `pytest` invocation **excludes `tests/e2e/playwright`**. Why: `pytest-playwright`'s sync browser API keeps a long-lived event loop under the hood (via greenlet), and the next async-marked test that `pytest-asyncio` tries to run after it sees `Runner.run() cannot be called from a running event loop`. The two plugins don't share a session cleanly. CI runs the suites in separate jobs (`test:` + `playwright:` in `.github/workflows/ci.yml`); locally the project default keeps the regular run clean.
