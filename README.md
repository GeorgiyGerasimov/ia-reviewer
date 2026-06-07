# ia-reviewer

[![CI](https://github.com/GeorgiyGerasimov/ia-reviewer/actions/workflows/ci.yml/badge.svg)](https://github.com/GeorgiyGerasimov/ia-reviewer/actions/workflows/ci.yml)
[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20NC%201.0.0-blue)](LICENSE)

Multi-agent AI **security** code reviewer for GitHub, built on LangGraph
and FastAPI. Runs four specialist reviewers in parallel, optionally
pauses for a human, and publishes a single consolidated Markdown report.

```
┌── Validate ─┬─→ Dependency    ─┐
              ├─→ Injection      ├─→ Aggregate ─→ Exploit proposals ─→ Publish
              ├─→ OWASP 10       │     (human-approved, capped)
              └─→ Configuration ─┘
```

Two modes share the same pipeline:

- **PR mode** — review a PR diff, post a top-level comment on the PR.
- **Repo mode** — shallow-clone a repo and walk it per-specialist
  (one LLM call per file), save a Markdown report locally and link to
  it from the chat. Active runs are visible in the UI's "Active reviews"
  panel and can be cancelled with a Stop button.

Architecturally inspired by a sibling code-review tool for GitLab+Slack,
but ia-reviewer is scoped to **GitHub only**, **without Slack**, and
**security-focused**.

> ⚠️ **Exploit proposals — defensive use only.** Critical/major findings
> trigger an optional human-gated step that generates a proof-of-concept
> artifact. This feature exists to let security engineers validate
> findings against **their own code** in an isolated environment and
> measure real risk. It is **NOT** for use against third-party systems
> and **NOT** for destructive purposes. The LLM is instructed to refuse
> third-party targets; every chat prompt, button row, report section,
> and sibling artifact file carries the same disclaimer. See
> `src/agents/exploit_proposal.py::EXPLOIT_DISCLAIMER` for the canonical
> wording.

## Quick start (Docker)

```bash
git clone https://github.com/<your-org>/ia-reviewer.git
cd ia-reviewer

cp .env.example .env
# minimum viable .env: USE_AI_GATEWAY=true, AI_GATEWAY_URL=<your OpenAI-compatible endpoint>
# leave GITHUB_TOKEN empty for anonymous clone of public repos

docker compose up -d --build
# open http://localhost:8000/  — review UI (React SPA served by nginx)
# open http://localhost:3000/  — Langfuse traces (dev@local.dev / localdev123!)
```

`docker compose up` brings up the full stack: backend (FastAPI), web
(nginx + Vite-built React SPA), Postgres (pgvector), and the Langfuse
trace stack (web + worker + ClickHouse + Redis + MinIO). The backend
container is `expose:`-only inside the compose network; nginx is the
sole host-facing surface on `${IA_PORT:-8000}`.

For a constrained host, skip the Langfuse stack:
`docker compose up -d app web postgres`.

Paste any GitHub URL into the form:

- `https://github.com/owner/repo` → whole-repo review at HEAD
- `https://github.com/owner/repo/tree/develop` → whole-repo review at `develop`
- `https://github.com/owner/repo/pull/42` → PR diff review

URLs with trailing `.git`, `/blob/…/file`, query strings, or fragments
are normalised server-side.

Or via curl:

```bash
curl -s -X POST http://localhost:8000/review \
  -H 'Content-Type: application/json' \
  -d '{"repo_url": "https://github.com/octocat/Hello-World"}'
# → {"status": "started", "thread_id": "...", "repo_url": "...", "ref": "HEAD"}

curl -s http://localhost:8000/reports/<thread_id>.md   # final report
```

## Four reviewers

- **Dependency** — manifest / lockfile vulnerabilities, typosquats,
  unmaintained packages. Script-first via OSV.dev in repo mode; one
  LLM call only for the narrative summary.
- **Injection** — SQLi, command, template, deserialization, path
  traversal, XSS. Source code only.
- **OWASP Top 10** — application-level OWASP (A01 / A02 / A04 /
  A07-logic / A08 / A09 / A10). Source code only.
- **Configuration** — misconfigurations, default credentials, exposed
  secrets, container/IaC hardening, reverse-proxy headers. Scans
  `Dockerfile`, `docker-compose*.yml`, `.env*`, `*.tf`, `nginx.conf`,
  plus generic INI / TOML / YAML / properties. A03 → Injection;
  A06 → Dependency; A05 misconfig + A07 default-credentials + secret
  exposure → Configuration.

Scope a subset with `scope=["dependency", "injection"]` on the request
body. Empty (default) runs all four.

## Architecture in a sentence

LangGraph state machine with a parallel fan-out across the four
reviewers, a Phase B human-in-the-loop re-review decision node bounded
by `MAX_CYCLES=3`, and on-demand exploit-proposal generation via
`POST /reviews/{tid}/exploits/{fid}` bounded by `MAX_EXPLOIT_PROPOSALS=3`.
Both human-loop branches degrade gracefully when no checkpointer is
wired (auto-skip interrupts). See [`docs/architecture.md`](docs/architecture.md)
for the full picture.

## RAG — retrieval over past findings

When `EMBEDDING_MODEL` is configured and Postgres is reachable, a
`retrieve_past_context` node sits between the validator and the
reviewer fan-out. It embeds a query derived from the incoming request
(`pr_url` + files for PR-mode, `repo_url` + tree listing for repo-mode),
runs a cosine-distance search against `review_findings.embedding` for
the same `target_url` filtered per role, and writes the top-K hits into
`state.past_findings_by_role`. Each security reviewer's prompt then
includes a "Previous findings on this repo" block — only their own role's
slice — so a recurring issue gets recognised rather than re-reported as
a fresh discovery.

Embeddings are written after `save_review` in a fire-and-forget
backfill (`ReviewStore.embed_pending`). The whole feature is fail-soft:
no `EMBEDDING_MODEL`, no `DATABASE_URL`, gateway without an
`/embeddings` endpoint, or transient network failure all collapse to
"no past context spliced this run" without affecting the report. See
[`.env.example`](.env.example) for the four `EMBEDDING_*` knobs and the
`RAG_TOP_K` cap.

## Web UI

The production UI is a React SPA at `web/` (Vite + TypeScript + shadcn/ui
+ Tailwind), served by a dedicated nginx container in compose. It shows:

- Live workflow diagram (PR-mode-aware — `clone_repo` is greyed out
  for PR reviews), circles light up as the graph progresses.
- Per-file scan panel (repo mode) with per-role progress bars.
- Critical findings panel with on-demand exploit PoC creation
  (bounded scroll, ~2-3 rows visible).
- Token usage panel (grand total leading, per-node breakdown
  collapsible).
- Final report rendered as Markdown.
- Past reviews + Active reviews lists in the sidebar.
- Header pills: theme toggle + Langfuse "View traces ↗" link.

The legacy Jinja template at `templates/index.html` is retained as
a debug fallback for direct `uvicorn` runs (developer-only); it is
NOT reachable through the compose stack — nginx terminates `/` and
serves the React bundle. See [`docs/ui.md`](docs/ui.md) for the
workflow colour codes and React component layout.

## Docker

```bash
cp .env.example .env
docker compose up -d --build   # app + postgres + langfuse-web + langfuse-worker + clickhouse + redis + minio
```

`--build` triggers **two independent images** with strict separation:

1. **`Dockerfile`** (backend) — `python:3.11-slim` carrying Python
   deps + app code. No frontend assets, no Node, no `web/`.
2. **`web/Dockerfile`** (frontend) — `node:22-alpine` builds the
   Vite SPA inside the image (no local Node needed), then
   `nginx:alpine` serves the bundle + reverse-proxies `/health`,
   `/review`, `/reviews/*`, `/reports/*`, `/chat/*`, `/img.png`,
   `/ws/*` to the `app` service. See `web/nginx.conf` for the
   exact map.

The two images share nothing source-level. Either can be redeployed
or moved to a different host (SPA to a CDN, backend behind a load
balancer) without code changes. To rebuild a specific side after
pulling new commits: `docker compose up -d --build app` or
`docker compose up -d --build web`.

The user-facing URL is unchanged: nginx exposes `${IA_PORT:-8000}`
on the host; the backend is `expose:`-only inside the compose
network.

Open <http://localhost:8000> for the review UI and <http://localhost:3000>
for Langfuse traces (login `dev@local.dev` / `localdev123!`, self-seeded on
first boot). The UI's header also shows a "View traces ↗" link wired to
the same URL.

See [`docs/observability.md`](docs/observability.md) for the Langfuse
stack details (web + worker + clickhouse + redis + minio). To skip
Langfuse on a constrained host, scope the up command:
`docker compose up -d app postgres`.

> ⚠️ **Dev credentials.** Every service starts with hard-coded
> placeholder passwords so the laptop demo works zero-touch. **They are
> not safe for any network-reachable deployment.** See
> [`docs/security-defaults.md`](docs/security-defaults.md) for the full
> list and regeneration recipes.

## Documentation

| Doc | What's in it |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Graph topology, state, agents, phases. |
| [`docs/api.md`](docs/api.md) | HTTP/WebSocket endpoints reference. |
| [`docs/pr-mode.md`](docs/pr-mode.md) | PR-review flow. |
| [`docs/repo-mode.md`](docs/repo-mode.md) | Repo-review flow, clone + snapshots, caps. |
| [`docs/configuration.md`](docs/configuration.md) | Every env var + in-code constant. |
| [`docs/observability.md`](docs/observability.md) | Langfuse setup, tracing wiring. |
| [`docs/security-defaults.md`](docs/security-defaults.md) | ⚠️ Every dev credential to replace before production. |
| [`docs/ui.md`](docs/ui.md) | Frontend layout + workflow colours. |
| [`docs/development.md`](docs/development.md) | Setup, TDD, testing, adding a reviewer. |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Symptoms-to-causes for real-world failures. |

## Tests

Backend pytest + ruff and frontend Vitest are dev-tools — they
run on the contributor's machine against the local checkout, not
inside the Docker image. See [`docs/development.md`](docs/development.md)
for setup; the short version:

```bash
# Backend
.venv/bin/pytest                  # full suite (~7s on M-series Mac)
.venv/bin/pytest tests/unit -q    # fast feedback loop
.venv/bin/ruff check src/ tests/

# Frontend (web/)
cd web && npm test                # full vitest suite (~3s)
cd web && npm run lint && npm run typecheck && npm run build
```

The project follows strict TDD; see
[`docs/development.md`](docs/development.md) and
[`.claude/skills/`](.claude/skills) for the conventions.

## Benchmarks

Beyond pass/fail tests, [`benchmarks/`](benchmarks/) measures **agent
accuracy** across a fixed corpus of input/expected pairs and reports a
single success-rate per suite. Three suites cover the three-modality
eval recipe (programmatic + tool-call + LLM-as-judge):

| Suite | Cases | What it exercises |
|---|---|---|
| `validator` | 14 | `RequestValidator` across all 7 categories — pure-code prefilter + LLM judge |
| `dependency` | 12 | `DependencyScanner` against OSV (mocked inline) — npm v1/v2, pip with extras / strict pins, mixed ecosystems, error paths |
| `judge` | 8 | `LLMJudge` calibration — measures the LLM-as-judge's accuracy on 3 deliberately-good + 5 deliberately-broken reports |

```bash
python -m benchmarks.run validator              # live LLM
python -m benchmarks.run validator --mock-llm   # measures prefilter alone
python -m benchmarks.run dependency             # always deterministic
python -m benchmarks.run judge                  # live judge (LLM)
python -m benchmarks.run judge --mock-llm       # measures calibration gap
python -m benchmarks.run all                    # all three suites
```

Current numbers:
- `dependency` = **100% (12/12)** with mocks (deterministic)
- `validator --mock-llm` = **86% (12/14)** — pure-code prefilter; the 14 pp gap to live LLM is the trolling-detection that requires the judge
- `judge --mock-llm` = **38% (3/8)** — constant-accept mock catches only the 3 good reports; the 62 pp gap to live judge is the value-add of LLM-as-judge over a naïve oracle

See [`benchmarks/README.md`](benchmarks/README.md) for adding a case +
CI wiring + the rationale behind the three-modality recipe.

## Performance and cost

Per-node wall-clock latencies are logged on a dedicated `graph.timing`
logger in a stable structured format:

```
node_complete node=injection_review thread_id=abc-123 duration_ms=2451 status=ok
```

Recipes (grep + awk for p50/p95, per-thread breakdown, error-only
filter) and cost / latency expectations per mode (PR / repo, small /
large) live in [`docs/performance-and-cost.md`](docs/performance-and-cost.md).

Headline expectations:

| Mode | p95 latency | LLM calls | Approx. cost (Sonnet 4.6) |
|---|---|---|---|
| PR-mode, simple diff | 8–15 s | 4–5 | $0.05–$0.15 |
| PR-mode, large diff | 15–30 s | 4–5 | $0.20–$0.50 |
| Repo-mode, small (≤ 50 files) | 60–120 s | ~100 | $1–$3 |
| Repo-mode, saturated (200 files) | 5–15 min | ~400 | $10–$40 |

Success rate from the three benchmark suites: dependency **100%**,
validator (mock) **86%**, judge (mock-gap floor) **38%** — see
[`benchmarks/README.md`](benchmarks/README.md) for live-LLM numbers.

## Security checklist

Honest position against the OWASP LLM Top 10 (2025). Full rationale,
code links, and known gaps in [`docs/security-checklist.md`](docs/security-checklist.md).

| # | OWASP LLM | Status | One-line |
|---|---|---|---|
| LLM01 | Prompt Injection | ⚠️ Open | Diff/source flow into prompts; LLM has zero agency → injection produces bad findings at worst, not code execution |
| LLM02 | Sensitive Information Disclosure | ⚠️ Partial | Local AI Gateway by default; no pre-LLM secret scan |
| LLM03 | Supply Chain | ⚠️ Partial | Model id pinning + deterministic OSV; no cert pinning |
| LLM04 | Data and Model Poisoning | ✅ Mitigated | RAG write path is system-only; filter by `target_url` |
| LLM05 | Improper Output Handling | ✅ Mitigated | Custom escape-first renderer; link allowlist; no LLM-driven execution |
| LLM06 | Excessive Agency | ✅ Mitigated | LLM returns data only; side effects are deterministic; exploit gated by human approval |
| LLM07 | System Prompt Leakage | ✅ Mitigated | No secrets in prompts |
| LLM08 | Vector and Embedding Weaknesses | ✅ Mitigated | Per-repo, per-role retrieval filter; dim mismatch fail-soft |
| LLM09 | Misinformation | ✅ (dependency) / ⚠️ (reviewers) | Script-first dep scan = byte-identical findings; reviewer text is best-effort |
| LLM10 | Unbounded Consumption | ✅ Mitigated | Five layers of bounds (diff size, file count, cycle count, timeouts, in-memory caps) |

Plus a project-specific baseline (PR1 + PR2 + operational secrets):

| Control | Where | Status |
|---|---|---|
| SSRF allowlist for repo URLs | `src/integrations/repo_fetcher.py::normalize_repo_url` | ✅ |
| Git env hardening (no creds prompt, shallow clone) | `repo_fetcher.clone_repo` | ✅ |
| WebSocket Origin allowlist (CSRF) | `main.chat_ws` | ✅ |
| Path-traversal guards (snapshot + reports) | `base_reviewer._read_snapshot_file`, `main.report_file` | ✅ |
| `/reviews` query validation | `main._parse_positive_int` | ✅ |
| Bounded in-memory stores (LRU FIFO) | `src/chat/store.py`, `progress_store.py` | ✅ |
| Exploit PoC defensive-use disclaimer (5 surfaces) | `exploit_proposal.EXPLOIT_DISCLAIMER` | ✅ |
| Dev defaults marked `🔐 REPLACE-BEFORE-PROD` | `.env.example`, `docker-compose.yml` | ✅ — see [`docs/security-defaults.md`](docs/security-defaults.md) |

**Out of scope (documented gaps):** per-user auth, rate limiting, audit
log, HTTPS, browser CSP. See [Threat model: not in scope](docs/security-checklist.md#threat-model-not-in-scope) for rationale.

## Status

Working end-to-end:

- Both review modes (PR + repo).
- **Four reviewers** in parallel (Dependency / Injection / OWASP /
  Configuration) with per-specialist whitelists in repo mode.
- Two-stage validator (pure-code + LLM judge), Phase B re-review,
  Phase C exploit-proposal with human approval + 60-second timeout.
- **Stop button** to cancel an in-flight review (`POST /reviews/{id}/cancel`).
- **Active reviews** panel: see all in-flight runs across browser tabs,
  click to switch into one.
- Resumable graph via `AsyncPostgresSaver` (or degraded-but-functional
  mode without a checkpointer).
- Langfuse tracing across initial reviews and resume-after-interrupt.
- Self-contained UI (no build step) with branch-aware workflow
  visualisation, dark theme (persisted), Markdown-table rendering for
  the Summary block.
- **3-tier UI testing**: static template lint + Playwright e2e (5
  scenarios). See [`docs/ui-testing.md`](docs/ui-testing.md).

## License

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20NC%201.0.0-blue)](LICENSE)

Released under the **PolyForm Noncommercial License 1.0.0** — see
[`LICENSE`](LICENSE) for the full text.

In plain words:

- ✅ **You may**: read, study, fork, modify, redistribute, contribute
  back, run for personal/hobby/research use, use in education, use in
  charities/NGOs/government/research institutions.
- ❌ **You may not**: use the software (or any derivative) for any
  commercial purpose — selling it, embedding it in a paid product,
  offering it as a paid SaaS, or using it inside a for-profit business
  in a way that generates revenue.

PolyForm Noncommercial is a **source-available** license — it does
**not** meet the OSI definition of "open source" because it forbids
commercial use. The source is fully open for inspection, learning,
and non-commercial work.

If you want a commercial license, contact the maintainer.

> Required Notice: Copyright Georgiy Gerasimov (ia-reviewer)
