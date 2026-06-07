# Configuration

All runtime configuration is loaded by `pydantic-settings` from
[`.env`](../.env.example) (or the process environment, which takes
precedence). Defaults live in [`src/utils/config.py`](../src/utils/config.py).

### Local overrides via `.env` (the public-repo workflow)

This repository is intended to be safe to publish as a public mirror,
so all defaults in `.env.example` and `src/utils/config.py` point at
sanitised, generic values (`localhost`, placeholder API keys, dev
secrets marked `🔐 REPLACE-BEFORE-PROD`). Your real, environment-
specific values — internal IPs, real API keys, corp-LAN gateway
URLs — go into your **local `.env` file**, which is `.gitignore`d and
never reaches GitHub.

The precedence order (highest → lowest):

1. Process environment variables (`AI_GATEWAY_URL=… python main.py`).
2. The local `.env` file at the project root (gitignored).
3. The class default in `src/utils/config.py`.

So you can keep developing against your real gateway by editing
`.env`:

```bash
# .env (gitignored)
AI_GATEWAY_URL=http://10.30.1.14:8006/v1   # your actual box
AI_GATEWAY_MODEL=Qwen/Qwen2.5-Coder-32B-Instruct
```

and `git status` will still show nothing — only the sanitised template
ever lives in git history.

## Required

| Variable | Required when | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | `USE_AI_GATEWAY=false` | Validated at startup. In gateway mode (default) this is skipped — the gateway handles auth. |

That's it for hard requirements. Everything else has a sensible default.

## LLM routing

The default `USE_AI_GATEWAY=true` routes every LLM call through an
OpenAI-compatible endpoint at `AI_GATEWAY_URL`. The actual model name
sent to that endpoint is resolved by
[`_resolve_gateway_model`](../src/models/factory.py) in three steps:

1. **Explicit pin** — `AI_GATEWAY_MODEL` if set (e.g.
   `Qwen/Qwen2.5-Coder-32B-Instruct`).
2. **Auto-discovery** — sync `GET <AI_GATEWAY_URL>/models`, take the
   first `data[].id`. Cached in module memory so the network is hit at
   most once per process. 5-second timeout.
3. **Bifrost fallback** — `provider/internal-name` mapping
   (`anthropic/claude-sonnet-4-6`, `openai/gpt-4o`, …) if both above
   fail. Useful when the gateway is actually Bifrost / LiteLLM rather
   than a single-model box.

So the same agent code works against:

- A local vLLM / llama.cpp box (auto-discovery picks the served model).
- A pinned local model (set `AI_GATEWAY_MODEL`).
- A Bifrost-style multi-provider proxy (leave both empty, mapping fires).
- `USE_AI_GATEWAY=false` + per-provider keys for direct
  Anthropic / OpenAI / Google calls.

Discover what your gateway exposes:

```bash
curl -s "$AI_GATEWAY_URL/models" | jq '.data[].id'
```

### Gateway settings

| Variable | Default | Purpose |
|---|---|---|
| `USE_AI_GATEWAY` | `true` | Route through `AI_GATEWAY_URL` instead of calling provider SDKs. |
| `AI_GATEWAY_URL` | `http://localhost:8001/v1` | OpenAI-compatible base URL. The default assumes a local serving box; real deployments override via the gitignored `.env`. |
| `AI_GATEWAY_API_KEY` | _(empty)_ | Optional bearer key. Omitted if empty. |
| `AI_GATEWAY_MODEL` | _(empty)_ | Pin a specific model id; bypasses auto-discovery. |

### Provider keys (only used when `USE_AI_GATEWAY=false`)

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude (Sonnet/Opus/Haiku). Required. |
| `OPENAI_API_KEY` | OpenAI (gpt-4o, gpt-4o-mini). Optional. |
| `GOOGLE_API_KEY` | Google (gemini-2.0-flash). Optional. |

## GitHub access

| Variable | Default | Required for | Purpose |
|---|---|---|---|
| `GITHUB_TOKEN` | _(empty)_ | PR-mode; private repos | Bearer token for the REST API; injected as basic-auth into HTTPS URLs for `git clone`. |
| `GITHUB_API_URL` | `https://api.github.com` | — | Override for GitHub Enterprise. |
| `GITHUB_WEBHOOK_SECRET` | _(empty)_ | Future webhook support | Not used in the current flow. |
| `ENABLE_GITHUB_MCP` | `false` | Reviewer repo-context | Reserved — not wired in the current flow. |
| `GITHUB_MCP_URL` | `http://localhost:3003/mcp` | — | Sidecar URL when MCP is on. |

**Public repos** work anonymously (60 req/h GitHub REST rate limit;
clone is unlimited). **PR-mode posts comments**, which requires a token
with `repo` or `public_repo` scope.

## Storage paths

| Variable | Default | Purpose |
|---|---|---|
| `REPORTS_DIR` | `reports` | Where `<thread_id>.md` files are written and where `GET /reports/<filename>` reads from. |
| `SNAPSHOTS_DIR` | `snapshots` | Root for per-review `git clone` tempdirs. Each clone goes into `tempfile.mkdtemp(prefix="ia-review-", dir=SNAPSHOTS_DIR)`. Cleanup removes the subdir in `_run_repo_review`'s `finally:` block. |
| `DATABASE_URL` | `postgresql://ia:ia@localhost:5432/ia_reviewer` | Postgres for **three** uses: LangGraph checkpointer (run state + interrupts), pgvector vectorstore (embeddings), and the `ReviewStore` audit tables (`reviews` + `review_findings`). Set to empty string to disable all three — the app still runs, just without checkpointing, semantic search, or `/reviews*` endpoints. |

Both `REPORTS_DIR` and `SNAPSHOTS_DIR` are resolved relative to the
process working directory at startup (cwd shouldn't change after that —
it doesn't, in our code). Pre-created in the FastAPI lifespan
(`mkdir(parents=True, exist_ok=True)`) so the first request doesn't
race on the directory.

## Caps

| Variable | Default | Caps |
|---|---|---|
| `MAX_FILES_PER_AGENT` | `200` | Per-reviewer file iteration in repo-mode (one LLM call per file). Files beyond the cap are skipped with a truncation note in the report. |
| `MAX_REPO_FILES_HARD` | `5000` | Total tree size hard cap; trees larger than this are rejected outright by the repo-mode validator (`oversized_repo`). |
| `VALIDATION_REJECT_THRESHOLD` | `7` | Reserved — used by validators that score 0–10. Not currently consulted by the LLM-judge implementation (which uses strict verdict strings). |
| `CONTEXT_TTL_HOURS` | `24` | Reserved — reviewer reference-materials cache. Not currently wired. |
| `SKIP_REVIEW_FOR_DOCS_ONLY` | _(empty)_ | CSV of reviewer roles to skip when the PR diff touches only documentation. Empty = never skip. |

In-code caps (not env-overridable):

| Constant | Where | Value | Caps |
|---|---|---|---|
| `MAX_FILE_BYTES` | [`repo_fetcher.py`](../src/integrations/repo_fetcher.py) | 200_000 | Per-file size during snapshot walk. |
| `MAX_CYCLES` | [`review_decision.py`](../src/agents/review_decision.py) | 3 | Initial pass + 2 human-approved reruns. |
| `MAX_EXPLOIT_PROPOSALS` | [`exploit_proposal.py`](../src/agents/exploit_proposal.py) | 3 | Records the on-demand exploit endpoint will persist per review (any status). Beyond this the endpoint returns 409. |

## Observability — Langfuse

| Variable | Default | Purpose |
|---|---|---|
| `LANGFUSE_PUBLIC_KEY` | _(empty)_ | Public key. **Both keys must be set** to enable tracing. |
| `LANGFUSE_SECRET_KEY` | _(empty)_ | Secret key. |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | Override for self-hosted. |

When both keys are present, `get_langfuse_callback()` returns a
`langfuse.langchain.CallbackHandler` and `_trace_config` attaches it to
every `graph.astream` / `graph.ainvoke` invocation. The `thread_id`
doubles as the Langfuse `session_id` so chat-driven resumes group with
the initial review.

Self-hosted Langfuse stack is **default-on** in `docker-compose.yml` —
`docker compose up -d` brings up `langfuse-web` + `langfuse-worker`
alongside `app` + `web` + `postgres`. See
[observability.md](observability.md) for the trace UI URL and
seeded dev keys.

## App

| Variable | Default | Purpose |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Effective level for `src.*` loggers and (post-lifespan) `uvicorn.*` loggers. |
| `ENVIRONMENT` | `development` | Free-text tag (unused programmatically). |

## Putting it together

A minimal working `.env` for a local public-repo demo against an
OpenAI-compatible gateway:

```bash
USE_AI_GATEWAY=true
AI_GATEWAY_URL=http://localhost:8001/v1   # or your real gateway endpoint
AI_GATEWAY_API_KEY=
AI_GATEWAY_MODEL=

GITHUB_TOKEN=                 # public repos only — anonymous clone
GITHUB_API_URL=https://api.github.com

ANTHROPIC_API_KEY=unused      # not actually used when USE_AI_GATEWAY=true
DATABASE_URL=                 # disable Postgres / checkpointer for local hacking

LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=

REPORTS_DIR=reports
SNAPSHOTS_DIR=snapshots
MAX_FILES_PER_AGENT=200
MAX_REPO_FILES_HARD=5000
LOG_LEVEL=INFO
ENVIRONMENT=development
```

For production / PR-mode / Langfuse tracing, fill in `GITHUB_TOKEN`,
`DATABASE_URL`, and the Langfuse keys.
