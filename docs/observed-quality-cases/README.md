# Observed quality cases

Field notes on how different LLMs perform when wired as the
back-end model for ia-reviewer. Each file documents one full review
run: what model was used, what came out, what was real and what was
hallucinated. The folder is intentionally informal — it's a logbook
for "what we've actually seen", not a benchmark suite (the suite
lives in [`benchmarks/`](../../benchmarks/)).

Use it to:
- pick a back-end model with a realistic expectation of accuracy,
- decide whether `LLMJudge` thresholds need tuning per-model,
- show the diploma committee a concrete example of why model size
  matters for free-text agent output.

Naming convention: `<model-id>-<YYYY-MM-DD>.md`. One file per
notable run. Keep the original report alongside (link or include a
`thread_id` so anyone can `curl /reviews/<id>`).

## Index

| Date | Model | Mode | Target | Headline result |
|---|---|---|---|---|
| 2026-06-05 | `gpt-oss-20b` (self-hosted) | repo | self-review of `ia-reviewer` | [77 findings, ~12% signal — ~70% noise + hallucinations](./gpt-oss-20b-2026-06-05.md) |
