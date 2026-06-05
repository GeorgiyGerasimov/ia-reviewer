# LLM-as-judge in production

## What we have today

The judge itself is shipped: [`src/evals/llm_judge.py`](../src/evals/llm_judge.py)
exposes `LLMJudge.evaluate(report_markdown, criteria) -> JudgeVerdict`.
It calls the configured LLM (same `ModelFactory` as everything else),
feeds in the report + rubric, parses the strict-JSON response, and
returns:

```python
@dataclass
class JudgeVerdict:
    passed: bool                              # True iff every required criterion was met
    overall_score: int                        # 0–10 holistic
    per_criterion: list[CriterionResult]      # per-rubric-item result
    rationale: str                            # one-sentence summary
```

The rubric is defined per call. The default rubric used by the
benchmark suite lives in [`benchmarks/judge/cases.json::criteria`](../benchmarks/judge/cases.json):

```json
[
  {"name": "has_overall_severity", "description": "..."},
  {"name": "mentions_all_roles", "description": "..."},
  {"name": "no_fake_cve_format", "description": "..."},
  {"name": "exploit_has_disclaimer", "description": "..."},
  {"name": "no_raw_json_in_body", "description": "..."}
]
```

The calibration suite (`benchmarks/judge/`) measures judge accuracy
against 8 labelled reports — 3 deliberately good + 5 deliberately
broken — so you can tell when the judge itself starts drifting.

### What's NOT wired yet

The judge **does not run automatically** during a normal review.
It's currently used in three places only:

1. **Benchmark suite** — `python -m benchmarks.run judge` measures
   judge accuracy against the calibration set.
2. **Unit tests** — `tests/unit/test_llm_judge.py` exercises the
   parser + fail-closed behaviour with mocked LLMs.
3. **Manual CLI** — `python -m src.evals.judge_report <thread_id>`
   (see [Pattern 1](#pattern-1-cli-on-demand) below) lets you
   score any past report on demand.

Reports published by the main pipeline reach disk + DB **without** a
judge verdict attached. There's no "judge: passed" banner in the UI,
no judge score column in the `/reviews` list, no Slack alert when a
report scores below threshold.

This is intentional — the judge is an eval tool, not yet a runtime
component. The four patterns below are the ways you'd promote it
into production. Pick one (or several) based on how strong a signal
the judge gives in your environment.

## Production wiring options

### Pattern 1 — CLI on demand

**Shipped today.** Lowest effort, immediate value.

```bash
# Score one specific report by thread_id
python -m src.evals.judge_report a6163689-4d61-425c-a0b9-002dd9e2a723

# Or score by file path directly
python -m src.evals.judge_report --file reports/a6163689-4d61-425c-a0b9-002dd9e2a723.md

# Score the N most recent reports from ReviewStore
python -m src.evals.judge_report --recent 10

# Use a custom rubric file (different from the calibration default)
python -m src.evals.judge_report a6163689... --criteria my-criteria.json
```

The CLI prints a per-criterion breakdown + overall pass/fail +
rationale. Exit code is `0` when `passed=true`, `1` otherwise — so
you can pipe it into shell scripts or cron.

**When to use:** spot-checking weird-looking reports, building
intuition for what the judge flags, calibrating the rubric against
your real output before automating.

**Cost:** ~$0.02–$0.05 per report (Sonnet 4.6 against a typical
1k-finding-line report). Negligible at hand-driven scale.

### Pattern 2 — Inline graph node after `publish_report`

Add `judge_report` as a graph node between `publish_report` and
`process_proposal` (or between `finalize_report` and `END`). Each
review gets a verdict before the user even sees the chat-side
broadcast.

```
... → publish_report → judge_report → process_proposal → ... → END
```

Wire the verdict into `state` (new field `judge_verdict:
JudgeVerdict | None`), persist it to `reviews.judge_verdict` JSONB
column, and surface a banner in the UI when `passed=False`.

**Pros:**
- Verdict ready by the time the user opens the report.
- One pass-through audit of every published artefact.
- The verdict goes into the same Langfuse trace as the rest of the
  pipeline — easy to correlate.

**Cons:**
- Adds ~2–4 seconds + one LLM call per review (~$0.05).
- A judge failure during the run is now a graph-level error path you
  have to handle (we already have fail-closed semantics; either let
  the verdict be `passed=False` with `parse error` evidence, or
  log-and-skip).
- Couples eval to the hot path — if you want to retroactively
  re-evaluate old reports against a new rubric, you need a separate
  flow anyway.

**When to use:** when you trust the judge enough that its verdict
should drive UI / merge-gating behaviour, and review throughput is
low enough to absorb the extra call.

**Schema migration** (one column):

```sql
ALTER TABLE reviews
  ADD COLUMN IF NOT EXISTS judge_verdict JSONB DEFAULT NULL;
```

### Pattern 3 — Periodic batch job (cron / scheduled task)

A background job runs every N minutes, picks up reviews with
`judge_verdict IS NULL`, scores them, writes the result back. The
review pipeline stays untouched — eval runs out-of-band.

```bash
# crontab / systemd timer
*/15 * * * * cd /app && python -m src.evals.judge_batch --limit 20
```

**Pros:**
- Pipeline stays simple and fast.
- Cheap to retroactively re-score old reports when the rubric
  changes (just `UPDATE reviews SET judge_verdict = NULL` and the
  batch picks them up).
- Easy to throttle / pause without affecting reviews.

**Cons:**
- Verdict isn't available at publish time. UI has to handle "no
  judge verdict yet" gracefully.
- Extra moving part (cron, timer, supervisor).
- Doesn't appear in the same Langfuse session as the review (separate
  trace tree per batch).

**When to use:** when reviews are frequent and you don't want a
critical-path LLM call, OR when you change the rubric often and want
to re-grade history.

### Pattern 4 — HTTP endpoint, judge-on-request

Add `POST /reviews/{thread_id}/judge` (or `GET /reviews/{thread_id}/judge`)
that runs the judge on-demand and returns the verdict.

```bash
curl -X POST http://localhost:8000/reviews/a6163689.../judge
# {"passed": false, "overall_score": 4, "per_criterion": [...], "rationale": "..."}
```

The UI gets a "Re-judge" button that calls this; CI / dashboards
can also poke it.

**Pros:**
- Zero impact on the review pipeline.
- User-driven — the human asks for the verdict when they want it.
- Easy to A/B test different rubrics or models without touching
  the rest.

**Cons:**
- No automatic monitoring (a bad report stays bad unless someone
  clicks the button).
- Same LLM-call cost as Pattern 1 but per-click.

**When to use:** transitional state, OR when you want to expose
judge access to other tools (CI scripts, monitoring dashboards)
without committing to either of the heavier patterns above.

## Recommended progression

For ia-reviewer right now I'd go in this order:

1. **Now (this PR):** ship the CLI (Pattern 1). It gives the
   operator a tool to grade existing reports without committing the
   architecture to anything.
2. **Soon (next iteration):** add Pattern 4 (HTTP endpoint) so the
   UI can offer a "Re-judge" button. Same code as the CLI, just a
   thin FastAPI wrapper. Lets you collect judge verdicts on real
   traffic without a schema migration.
3. **When you have ≥100 real reports and trust the judge:** add the
   `judge_verdict` JSONB column + Pattern 2 (inline graph node) so
   every new review is graded automatically. Backfill the old
   reports via Pattern 3 (one-off batch).
4. **Once judges are stable across runs:** add a UI banner that
   prominently warns the user when `passed=False`, and consider
   gating CI / merge actions on the verdict.

Pattern 2 + 3 work well together: pattern 2 keeps current reviews
fresh, pattern 3 retroactively rescores when the rubric changes.

## Picking a model for the judge

The judge is itself an LLM call, so it has the same "model size
matters" problem as the reviewer. From the [observed quality
cases](./observed-quality-cases/):

| Judge model | Calibration accuracy (`benchmarks/judge`) | Notes |
|---|---|---|
| `--mock-llm` (constant accept) | 38% (3/8) | Baseline floor — the calibration set is *built* to fail this. |
| `gpt-oss-20b` (self-hosted) | not yet measured live; expect ≤ 60% | Likely to over-flag and under-detect; produces inconsistent rationales. |
| Claude Sonnet 4.6 (target) | not yet measured live; expect ≥ 75% | Calibration target documented in `benchmarks/README.md`. |
| Claude Opus 4 / GPT-4o | not yet measured live; expect ≥ 85% | Higher cost; reserve for the judge if reviewer is on a cheaper tier. |

**You can use different models for the reviewer and the judge.**
A common pattern is reviewer = cheaper/faster (Sonnet, or even a
self-hosted model), judge = top-tier (Opus / GPT-4o) — the judge
runs once per review, so it can absorb the higher per-call cost.

The judge model is configurable via one env var — **no code changes
needed:**

```bash
# .env (gitignored)
AI_GATEWAY_MODEL=Qwen/Qwen2.5-Coder-32B-Instruct   # reviewers use this
JUDGE_MODEL=claude-opus-4-7                         # judge uses this (override)
```

Resolution order inside `LLMJudge.__init__`:

1. Explicit `model_name=` argument → used as-is (lets tests + pinned
   A/Bs override everything).
2. `settings.JUDGE_MODEL` env var → used when constructor arg is
   omitted.
3. Built-in default (`claude-sonnet-4-6`) → safe fallback so the
   judge works out of the box with no config.

Empty `JUDGE_MODEL` (blank line in `.env`) is treated as "not set" —
falls through to the constructor arg or the built-in default.

Programmatic override still works for special cases:

```python
# A/B testing two judges on the same report:
v_opus = await LLMJudge(model_name="claude-opus-4-7").evaluate(report, criteria)
v_gpt  = await LLMJudge(model_name="gpt-4o").evaluate(report, criteria)
```

## What the judge protects you from

Concrete things that happened in our observed-quality runs that a
working judge would have caught:

- The `gpt-oss-20b` run produced an approved exploit PoC against
  **fabricated code** that doesn't exist in the project. A judge
  criterion *"the exploit references real file paths that exist in
  the repo"* would have flagged this.
- That same run generated 10 findings for **hallucinated files**
  (`src/auth.py`, `src/server.py`, etc.). A criterion *"every
  finding cites a file path that exists in the repo"* would have
  flagged them.
- The run reported **5 known/intentional design choices** as
  findings (no authn, no HTTPS, etc.). A criterion *"the finding
  is not already documented as out-of-scope in
  `docs/security-checklist.md`"* would have flagged them.

These three criteria aren't in the current calibration set — they'd
need to be added when you wire judge into production. The current 5
criteria cover format/quality; the suggested 3 cover content
authenticity. Both layers matter.

## Cost guard

Judge cost per run (Sonnet 4.6 pricing):

- Report length: typical ~5–30 KB (~1k–5k tokens input).
- Rubric block: ~500 tokens.
- Judge response: ~500–1500 tokens output.
- **Per-call cost: ~$0.02–$0.05.**

At 100 reviews/day with Pattern 2 = ~$60/month. At 1000/day = $600/month.
If that's too much, switch to Pattern 3 with `--limit 50` per hour, or
Pattern 4 (human triggers it on demand).

## Failure modes to plan for

- **Judge timeout / network error**: fail-closed (`passed=False`,
  evidence = `"judge unavailable"`). Already implemented in
  `LLMJudge._parse_response`. Means a transient gateway hiccup
  shows up as "review failed quality check" rather than silently
  passing.
- **Judge model swap**: changes calibration. Re-run
  `benchmarks/judge` and update the calibration baseline in
  `benchmarks/README.md` before promoting to production.
- **Rubric drift**: when you add a new criterion, add a calibration
  case that exercises it both ways (good + broken). Otherwise you
  can't tell if the new criterion is too strict or too loose.

## Related docs

- [`src/evals/llm_judge.py`](../src/evals/llm_judge.py) — the judge class itself.
- [`benchmarks/README.md`](../benchmarks/README.md) — calibration suite + numbers.
- [`benchmarks/judge/cases.json`](../benchmarks/judge/cases.json) — current criteria + calibration reports.
- [`docs/observed-quality-cases/`](./observed-quality-cases/) — field notes on actual model runs.
- [`docs/security-checklist.md`](./security-checklist.md) — what counts as
  in-scope finding vs documented gap.
