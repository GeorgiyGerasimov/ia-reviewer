# Performance, cost, and quality — expectations + measurement

This document fixes the **expected** numbers for ia-reviewer and
describes how to **measure** the actual ones from the structured logs
the graph emits. No live numbers are baked in — they depend on which
LLM is wired, network conditions, and repo size — but the orders of
magnitude and the math are pinned here so a regression is obvious.

## TL;DR — what to expect

| Mode | p95 latency (end-to-end) | LLM calls / run | Approx. cost (Sonnet 4.6) |
|---|---|---|---|
| **PR-mode, simple diff (≤ 200 LoC)** | 8–15 s | 4–5 | $0.05–$0.15 |
| **PR-mode, large diff (≥ 1k LoC, near 200k char cap)** | 15–30 s | 4–5 | $0.20–$0.50 |
| **Repo-mode, small (≤ 50 matching files)** | 60–120 s | ~100 | $1–$3 |
| **Repo-mode, large (`MAX_FILES_PER_AGENT=200` saturated)** | 5–15 min | ~400 | $10–$40 |

| Component | Success rate (current) | Source |
|---|---|---|
| Dependency benchmark (deterministic) | **100% (12/12)** | `benchmarks/dependency` |
| Validator benchmark (`--mock-llm`, pure-code only) | **86% (12/14)** | `benchmarks/validator` |
| Validator benchmark (live LLM, expected) | **95–100%** | extrapolation, not measured |
| Judge benchmark (`--mock-llm`, calibration gap) | **38% (3/8)** | `benchmarks/judge` |
| Judge benchmark (live LLM, target) | **≥ 75%** | calibration target |

These numbers assume **Claude Sonnet 4.6** as the back-end model. Other
models scale roughly linearly with their tokens-per-second + per-token
price; the structural break-down below is what's stable.

## Per-node expected latency

The graph has 11 nodes wrapped with `timed_node(...)` from
[`src/utils/node_timing.py`](../src/utils/node_timing.py). Each emits
one structured log line per invocation (see [Reading the logs](#reading-the-logs)).

Listed in execution order. "PR" = PR-mode review against a unified
diff; "Repo" = full-repo scan via `git clone --depth=1`.

| Node | PR mode | Repo mode | Notes |
|---|---|---|---|
| `validate_request` | 0–100 ms (prefilter) **or** 1–3 s (LLM judge) | 0–10 ms (size check only) | LLM judge only fires when prefilter is inconclusive. Repo-mode validator has no LLM branch. |
| `notify_rejection` | 100–500 ms | 5–50 ms | PR mode posts a GitHub comment (network); repo mode just writes a local file. |
| `retrieve_past_context` | 50–300 ms **or** 0 ms (RAG off) | 50–300 ms | 1 embed call + 3 SQL queries (one per role). 0 ms when `EMBEDDING_MODEL=""`. |
| `dependency_review` | 1–3 s | 1–3 s + (~50–500 ms OSV batch) | Repo mode is script-first: manifest parse + 1 OSV batch + 1 LLM summary call. |
| `injection_review` | 1–3 s | N × (1–3 s) where N ≤ `MAX_FILES_PER_AGENT=200` | One LLM call per matched file in repo mode. |
| `owasp_review` | 1–3 s | N × (1–3 s) where N ≤ `MAX_FILES_PER_AGENT=200` | Same shape as injection. |
| `configuration_review` | 1–3 s | M × (1–3 s) where M ≤ `MAX_FILES_PER_AGENT=200` | Per-file LLM on Dockerfile / compose / `.env` / `*.tf` / `*.yml` / `*.toml` / `*.ini`. Typically much smaller match-set than injection/owasp. |
| `review_decision` | 0–10 ms (heuristic) | 0–10 ms | Pure-code unless interrupted; on rerun the path loops back. |
| `aggregate_results` | 1–20 ms | 1–50 ms | Pure render (no I/O). |
| `format_report` | 0 ms (flag off) **or** 1–2 s (flag on) | same | LLM TL;DR copywriter. Off by default; gated by `ENABLE_REPORT_FORMATTER` and an optional inline judge check. |
| `publish_report` | 100–500 ms | 10–50 ms | PR mode: GitHub API. Repo mode: disk only. End of the graph. |

Out-of-band:
- `POST /reviews/{tid}/exploits/{fid}` — 2 LLM calls (draft + artifact), ~3–8 s per click. Capped at `MAX_EXPLOIT_PROPOSALS=3` per review.

The reviewers fan out in parallel — repo-mode `injection` + `owasp`
+ `configuration` run concurrently, so the slow path is
`max(injection_time, owasp_time, configuration_time)`, not their sum.
`dependency` is script-first and typically finishes before any of them.

## Reading the logs

Every node emits one line on the `graph.timing` logger:

```
2026-06-05 17:42:08,123 | INFO     | graph.timing | node_complete node=validate_request thread_id=abc-123 duration_ms=87 status=ok
```

Field order is **stable** (downstream parsers depend on it):

    node_complete node=<name> thread_id=<id> duration_ms=<int> status=ok | status=error error=<repr>

On exception the line shows `status=error error="<exc message>"` and
the exception then propagates (the wrapper logs and re-raises).

### Recipes

Pull every timing line for one review:

```bash
grep "thread_id=abc-123" logs/app.log | grep node_complete
```

p50 / p95 across the whole log for one node:

```bash
grep "node=injection_review" logs/app.log \
  | grep node_complete \
  | awk -F'duration_ms=' '{print $2}' \
  | awk '{print $1}' \
  | sort -n \
  | awk 'BEGIN{c=0} {a[c++]=$1} END{
      print "p50:", a[int(c*0.5)], "ms"
      print "p95:", a[int(c*0.95)], "ms"
      print "max:", a[c-1], "ms"
      print "n:", c
    }'
```

Per-node totals for one review (chain a `tee` to dump to CSV for
plotting):

```bash
grep "thread_id=abc-123" logs/app.log \
  | grep node_complete \
  | awk -F' ' '{print $5, $7}' \
  | sed 's/node=//; s/duration_ms=//'
```

Errors only — useful when chasing a failed review:

```bash
grep node_complete logs/app.log | grep "status=error"
```

The format is deliberately plain `key=value` (not JSON) so `awk` works
without any parser; `jq` users who want JSON output can pipe through
`awk` to reshape.

## Where the cost goes

A single LLM call to Sonnet 4.6 (per Anthropic's published pricing):

| | Tokens per call | Sonnet 4.6 price |
|---|---|---|
| Input | 5k–15k (depends on diff/file size) | $3 / 1M tokens |
| Output | 500–2 000 (JSON findings) | $15 / 1M tokens |
| **Avg cost per call** | | **$0.03–$0.10** |

Per-mode budget breakdown:

**PR mode** (one diff, all reviewers see the same diff):

| Step | Calls | Avg cost |
|---|---|---|
| `validate_request` (LLM judge branch only) | 0 or 1 | $0–$0.05 |
| `retrieve_past_context` (embed) | 1 embed | ≈ $0 (local model) |
| `dependency_review` | 1 | $0.03–$0.10 |
| `injection_review` | 1 | $0.03–$0.10 |
| `owasp_review` | 1 | $0.03–$0.10 |
| `configuration_review` | 1 | $0.03–$0.10 |
| `review_decision` (LLM only on rerun) | 0–2 (cap MAX_CYCLES=3) | $0–$0.20 |
| `format_report` (TL;DR — off by default) | 0–2 (incl. inline judge) | $0–$0.05 |
| Exploit endpoint (per click — out-of-band) | 0–2 × 3 (cap MAX_EXPLOIT_PROPOSALS=3) | $0–$0.60 |
| **Typical PR run** | | **$0.05–$0.50** |

**Repo mode** (per-file LLM calls in `injection` + `owasp` +
`configuration`):

| Step | Calls | Avg cost |
|---|---|---|
| `validate_request` | 0 | $0 |
| `retrieve_past_context` | 1 embed | ≈ $0 |
| `dependency_review` | 1 (LLM summary only) | $0.03–$0.10 |
| `injection_review` | N files × 1 call, N ≤ 200 | $3–$20 |
| `owasp_review` | M files × 1 call, M ≤ 200 | $3–$20 |
| `configuration_review` | K files × 1 call, K ≤ 200 (usually K ≪ N, M) | $0.20–$3 |
| `review_decision` | 0–2 | $0–$0.20 |
| `format_report` | 0–2 (incl. inline judge) | $0–$0.05 |
| Exploit endpoint (out-of-band) | 0–6 | $0–$0.60 |
| **Typical repo run, N=M=50, K=10** | | **$1–$4** |
| **Saturated repo run, N=M=K=200** | | **$10–$50** |

Use `MAX_FILES_PER_AGENT` (default 200) to clamp the worst case. Drop
it to 50 if you're cost-sensitive — the truncation note will appear in
the final report so the operator sees coverage is partial.

## Success rate sources

Three benchmark suites give measurable, reproducible accuracy numbers
([`benchmarks/`](../benchmarks/README.md) for the full description):

```bash
python -m benchmarks.run all
```

Last locally-observed numbers:

| Suite | Mode | Result |
|---|---|---|
| `dependency` | OSV mocked | **100% (12/12)** — deterministic |
| `validator` | `--mock-llm` | **86% (12/14)** — pure-code prefilter coverage |
| `judge` | `--mock-llm` | **38% (3/8)** — calibration-gap floor |

The `validator` and `judge` suites *also* run with live LLM, but live
numbers are model-dependent (Sonnet 4.6 vs. Haiku vs. a self-hosted
gateway) and we don't pin them here — run the suite to get yours.

A regression in any of these numbers below the documented floor is a
quality alarm worth investigating.

## How to measure for real

The structured log + benchmark suite combo is the whole story:

1. **Latency per node** → grep `graph.timing` log lines, awk on
   `duration_ms`. The `Recipes` block above has copy-paste commands.
2. **Cost per run** → sum the LLM-call counts from the log (one per
   `injection_review` / `owasp_review` invocation in PR mode; N per
   call in repo mode), multiply by the per-call price above. Or read
   actual token usage from Langfuse traces (UI link in the app
   header) for a real number.
3. **Success rate** → run the benchmark suite. CI can gate on
   `python -m benchmarks.run all --min-success-rate 0.80`.

Langfuse covers the LLM-level details (per-call token counts, full
prompt + response, model latency) — it complements the structured
log, which covers the graph-level orchestration (which nodes ran, in
what order, how long each took).

## Out of scope (not measured here)

- **Memory footprint.** Bounded by `MAX_FILES_PER_AGENT × MAX_FILE_BYTES`
  (≈ 40 MB worst case per reviewer's accumulated state). LangGraph
  checkpoints serialise to JSON in Postgres so steady-state memory
  doesn't grow with thread count.
- **Concurrent throughput.** Single FastAPI worker; one review at a
  time per thread_id, but `BackgroundTasks` queue them. For real
  throughput, scale with uvicorn workers behind a reverse proxy.
- **Cold-start latency.** First request after lifespan startup pays
  the model-discovery hit (`/v1/models` GET) and Postgres pool warm-up
  — typically adds 200–500 ms once per process.
