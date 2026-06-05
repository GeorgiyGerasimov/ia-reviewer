"""LLMJudge — evaluate a security review report against a rubric.

This is the third leg of the eval triangle for the diploma project:

  1. Programmatic asserts  → `tests/` (pass/fail gate)
  2. Tool-call benchmarks  → `benchmarks/dependency` (deterministic
                             output measured against fixture)
  3. LLM-as-judge          → THIS module — measure free-text quality
                             that programmatic asserts can't capture

The judge consumes a finished review markdown + a list of `Criterion`
objects and asks an LLM to score the report per-criterion. The
response shape is strict JSON, parsed into a `JudgeVerdict`. Fail-
closed on any parse failure — a broken judge IS a quality signal we
don't want to swallow.

The judge does not participate in production review traffic. It runs
offline against finished reports (chunk-replayed from `reports/`, or
via the `benchmarks/judge` suite for a calibration baseline).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from src.models.factory import ModelFactory
from src.utils.config import settings
from src.utils.llm_parsing import strip_thinking_trace
from src.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-6"

# Strip optional ```json ... ``` fences before json.loads.
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class Criterion:
    """One yes/no criterion in the judge's rubric.

    Keep `description` precise and observable — the judge interprets it
    literally. Bad: "the report is good". Good: "the report has a
    `**Overall severity:**` header line."
    """

    name: str
    description: str


@dataclass
class CriterionResult:
    """The judge's verdict on one criterion.

    `evidence` is the LLM's short justification (e.g. "line 3 has the
    header") — both human-readable and machine-readable enough for
    aggregation across many reports.
    """

    name: str
    met: bool
    evidence: str


@dataclass
class JudgeVerdict:
    """Aggregated judge output for a single report.

    `passed` is True iff every criterion was met. `overall_score` is
    the judge's free-form 0-10 holistic rating — useful for trend
    monitoring even when `passed` stays at a steady state.
    """

    passed: bool
    overall_score: int
    per_criterion: list[CriterionResult]
    rationale: str


_PROMPT_TEMPLATE = """You are evaluating a finished security-review report against
a fixed rubric. Your job is to decide, per criterion, whether the
report meets it — based ONLY on what you can see in the text below.

Be strict but fair: only mark a criterion `met: false` if you can point
to specific evidence in the report (a missing section, a malformed
identifier, a contradiction). Do NOT invent extra criteria.

Respond with one JSON object inside a single fenced block. No prose
outside the fence.

```json
{{
  "passed": true | false,
  "overall_score": 0-10,
  "per_criterion": [
    {{"name": "<criterion name>", "met": true | false, "evidence": "<short justification>"}}
  ],
  "rationale": "<one-sentence summary>"
}}
```

Criteria to evaluate:
{criteria_block}

Report:
---
{report}
---
"""


class LLMJudge:
    """LLM-backed rubric evaluator.

    Construct once per process; calls are independent. The judge uses
    the same `ModelFactory` as the rest of the agents so it shares
    gateway routing + Langfuse tracing without extra wiring.
    """

    def __init__(self, model_name: str | None = None):
        # Resolution order (first non-empty wins):
        #   1. explicit `model_name=` arg          — tests + pinned A/B
        #   2. `settings.JUDGE_MODEL` env var      — operator override
        #   3. built-in default                    — safe fallback
        # Empty env = "act as if not set" so blank lines in .env don't
        # accidentally override the constructor arg.
        self.model_name = (
            model_name
            or (settings.JUDGE_MODEL or "")
            or _DEFAULT_MODEL
        )
        self.model = ModelFactory.get(self.model_name)

    async def evaluate(
        self,
        report: str,
        criteria: list[Criterion],
    ) -> JudgeVerdict:
        """Run the rubric on `report`. See module docstring for the contract."""
        prompt = _PROMPT_TEMPLATE.format(
            criteria_block=_render_criteria(criteria),
            report=report,
        )
        response = await self.model.ainvoke(prompt)
        return _parse_response(response.content, criteria)


def _render_criteria(criteria: list[Criterion]) -> str:
    """Format the rubric as a numbered bullet list — gives the judge a
    stable ordering it can mirror in `per_criterion`."""
    return "\n".join(
        f"  {i}. `{c.name}` — {c.description}" for i, c in enumerate(criteria, start=1)
    )


def _parse_response(content: str, criteria: list[Criterion]) -> JudgeVerdict:
    """Strict JSON parse with fenced-block extraction. Fail-closed on any
    error — see module docstring."""
    payload = _extract_json_text(content)
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("LLMJudge: failed to parse judge response (%s): %.200r", e, content)
        return _parse_failed_verdict(f"parse error: {e}")

    if not isinstance(data, dict):
        return _parse_failed_verdict("top-level JSON is not an object")

    per_criterion_raw = data.get("per_criterion") or []
    if not isinstance(per_criterion_raw, list):
        return _parse_failed_verdict("per_criterion is not a list")

    per_criterion: list[CriterionResult] = []
    for entry in per_criterion_raw:
        if not isinstance(entry, dict):
            continue
        per_criterion.append(
            CriterionResult(
                name=str(entry.get("name", "")),
                met=bool(entry.get("met", False)),
                evidence=str(entry.get("evidence", "")),
            )
        )

    overall = data.get("overall_score", 0)
    try:
        overall_int = max(0, min(10, int(overall)))
    except (TypeError, ValueError):
        overall_int = 0

    passed = bool(data.get("passed", False))
    rationale = str(data.get("rationale", "") or "")
    return JudgeVerdict(
        passed=passed,
        overall_score=overall_int,
        per_criterion=per_criterion,
        rationale=rationale,
    )


def _extract_json_text(content: str) -> str:
    """Pull the JSON body out of an optional ```json ... ``` fence, or
    return the trimmed content as-is.

    Pre-strips any reasoning-model thinking trace (`</think>`-fenced)
    before fence detection. This handles Qwen3.6-27B / DeepSeek R1 /
    o1-style judges whose response is `<long-thinking>\n</think>\n{json}`.
    Same pattern as `BaseReviewer._parse_response`.
    """
    content = strip_thinking_trace(content)
    match = _FENCED_JSON_RE.search(content)
    if match:
        return match.group(1)
    return content.strip()


def _parse_failed_verdict(reason: str) -> JudgeVerdict:
    """Build the canonical fail-closed verdict for parser errors."""
    return JudgeVerdict(
        passed=False,
        overall_score=0,
        per_criterion=[
            CriterionResult(
                name="judge_response_parseable",
                met=False,
                evidence=f"parse failure: {reason}",
            )
        ],
        rationale=f"judge response could not be parsed ({reason})",
    )
