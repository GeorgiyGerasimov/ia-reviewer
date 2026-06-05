# Observability — Langfuse

ia-reviewer ships Langfuse tracing wired into every LLM call. Each
review thread is one Langfuse **session** keyed by `thread_id`; reviewer
LLM calls, validator judge, exploit-proposal drafts, and resume-after-
interrupt all fold into that session so you can replay a full review
in a single Langfuse trace.

## Quick start — local self-hosted stack

```bash
docker compose --profile observability up -d
open http://localhost:3000           # dev@local / localdev123!
```

This brings up 5 extra containers alongside `postgres` + `app`:

- `langfuse-web` — UI on port 3000.
- `langfuse-worker` — background ingestion.
- `clickhouse` — analytics events store.
- `redis` — queue between web and worker.
- `minio` — S3-compatible blob store for event payloads.

On first boot Langfuse self-seeds an org, project, and user with the
keys pre-baked in `.env.example`:

```
LANGFUSE_PUBLIC_KEY=pk-lf-dev
LANGFUSE_SECRET_KEY=sk-lf-dev
LANGFUSE_HOST=http://localhost:3000
```

So traces start flowing immediately — no UI login required before the
first review.

The seeded user (`dev@local` / `localdev123!`) is provisioned via
`LANGFUSE_INIT_USER_*` env vars on `langfuse-web`. Change the dev
defaults for any deployment that's reachable beyond localhost.

## Cloud (alternative)

Skip the profile. In `.env`:

```
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=pk-lf-<your-real-key>
LANGFUSE_SECRET_KEY=sk-lf-<your-real-key>
```

`docker compose up` then only starts `postgres` + `app` and traces ship
to cloud.langfuse.com.

## Off

Blank out the two key variables (or unset them):

```
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
```

`get_langfuse_callback()` returns `None` and `_trace_config` omits the
callback wiring. Lifespan logs `Langfuse tracing disabled (LANGFUSE_*
keys not set)` at startup so you can confirm.

## How tracing is wired

[`src/utils/tracing.py`](../src/utils/tracing.py) does the import-and-
construct dance. It supports two Langfuse client versions:

- **v4+** — instantiates `Langfuse()` and constructs
  `langfuse.langchain.CallbackHandler` against it.
- **v2/v3 fallback** — older `langfuse.callback.CallbackHandler`.

Missing keys, missing package, or init failure all return `None`. The
app never crashes because Langfuse is unreachable.

[`main.py::_trace_config(app, thread_id, state, trigger)`](../main.py)
composes the LangGraph invocation config:

```python
config = {"configurable": {"thread_id": thread_id}}
if handler:
    config["callbacks"] = [handler]
    config["metadata"] = {
        "session_id": thread_id,         # groups all traces from this review
        "user_id": state.request.author, # PR author or repo-mode "bot"
        "pr_url" or "repo_url": ...,     # whichever applies
        "tags": ["security-review", trigger],   # trigger ∈ {"http", "http_repo", "resume"}
    }
```

Both initial review (`trigger="http"` / `"http_repo"`) and resume-
after-interrupt (`trigger="resume"`) go through the same helper, so
every LLM call across the whole review thread groups under one Langfuse
session.

## What you see in Langfuse

For each review:

- **Session** — keyed by `thread_id`, all activity grouped together.
- **Traces** — one per `astream` invocation (initial + each resume).
- **Spans** — per LangGraph node, with inputs/outputs.
- **Generations** — every `ChatModel.ainvoke` call: prompt, completion,
  token usage, latency.

This makes "why did this finding surface?" or "why was this PR
rejected?" answerable in seconds — open the session, click the
relevant node, read the prompt and the model's JSON reply.

## Customizing the trace metadata

If you want to add fields (e.g. team name, custom tag), extend
`_trace_config` — that's the one place where every LLM-invoking entry
point composes its metadata. The function runs for both the initial
review and every resume so changes apply uniformly.
