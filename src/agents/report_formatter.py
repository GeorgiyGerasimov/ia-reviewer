"""ReportFormatter — optional LLM-copywriter polish on top of the
deterministic report layout produced by `aggregate_results`.

Graph position: `aggregate_results → format_report → publish_report`.
The node is ALWAYS in the graph; when `settings.ENABLE_REPORT_FORMATTER`
is False, `run()` short-circuits to `{}` (no LLM call, no state mutation).
This keeps the topology stable across configurations — operators can
flip the flag at runtime without rebuilding the graph.

When the flag is on AND the report has any findings, the formatter:

1. Builds a tight prompt with the structured findings list (file, role,
   severity, issue text) — never the full Markdown body.
2. Hard-rules the LLM: do NOT invent files / CVE ids / severities; do
   NOT add findings; 2-3 paragraphs, no bullets, no fences, no JSON.
3. Caps the response at `settings.REPORT_FORMATTER_MAX_CHARS`. Over the
   cap → dropped silently (fail-soft).
4. Splices the result into `state.final_report` via
   `ReportRenderer.splice_tldr` and stores it in `state.report_tldr`
   so `finalize_exploits` can re-splice after the exploit-loop re-render.

Fail-soft contract: ANY exception, an empty response, or an oversized
response → return `{}`. The deterministic report stays the published
version. The agent NEVER blocks publishing.

Hallucination guardrail: this agent is the canonical use-case for
running `LLMJudge` over its own output (see
docs/judge-in-production.md). That wiring is intentionally NOT done
here — it would double the per-call cost. Operators who want it can
wrap `_call_llm` in a follow-up commit; the existing fail-soft path
already swallows any judge-style rollback.
"""

from __future__ import annotations

from src.agents.report_renderer import ReportRenderer
from src.graph.state import AgentReview, ReviewState
from src.models.factory import ModelFactory
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_MODEL = "claude-sonnet-4-6"

_PROMPT_TEMPLATE = """You are writing a short TL;DR for a security review.
You will be given a structured list of findings. Your job is to write
2-3 short paragraphs (no bullet points, no fenced blocks, no JSON) that
help a busy reader understand what was found and where to focus.

Hard rules — violating any of these makes your output unusable:
1. Do not invent file paths, CVE numbers, package names, or line numbers.
   Use ONLY what is in the findings list below.
2. Do not change, alter, or modify any severity classification. If a
   finding says `severity: major`, do not call it critical.
3. Do not add findings that are not in the list. Do not extrapolate
   ("if this is exploitable then..."). Stick to what is there.
4. If you are uncertain about something, omit it rather than guess.
5. Write plain Markdown prose, 2-3 paragraphs, no headers, no bullets.

Findings ({total} total across {n_roles} reviewers):

{findings_block}

Now write the TL;DR following the rules above:
"""


class ReportFormatter:
    """Optional LLM polish layer for review reports."""

    def __init__(self, model_name: str | None = None):
        # Resolution order mirrors LLMJudge: explicit arg → env override
        # → built-in default. The model is constructed lazily on first
        # `run()` when enabled — keeps `__init__` cheap so the agent can
        # always be added to the graph without paying a factory cost.
        self._model_name_override = model_name
        self._model = None

    def _resolve_model(self):
        if self._model is not None:
            return self._model
        model_name = (
            self._model_name_override
            or (settings.REPORT_FORMATTER_MODEL or "")
            or _DEFAULT_MODEL
        )
        self._model = ModelFactory.get(model_name)
        return self._model

    async def run(self, state: ReviewState) -> dict:
        """Generate + splice the TL;DR. Returns `{}` on any fail-soft path."""
        if not settings.ENABLE_REPORT_FORMATTER:
            return {}

        reviews = state.agent_reviews or []
        total_findings = sum(len(r.findings) for r in reviews)
        if total_findings == 0:
            # Nothing to summarise — the deterministic "no findings"
            # rendering is already clear. Skip the LLM call entirely.
            return {}

        if not state.final_report:
            # aggregate_results should have populated this. If it didn't,
            # we have nothing to splice into; bail rather than crash.
            logger.warning(
                "format_report: state.final_report is empty (aggregate did not run?)"
            )
            return {}

        prompt = _build_prompt(reviews)
        tldr_text = await self._call_llm_safely(prompt)
        if not tldr_text:
            # _call_llm_safely already logged the reason.
            return {}

        new_body = ReportRenderer().splice_tldr(state.final_report, tldr_text)
        return {"report_tldr": tldr_text, "final_report": new_body}

    async def _call_llm_safely(self, prompt: str) -> str:
        """Wrap the LLM call with full fail-soft semantics.

        Returns:
          - The trimmed response content on success.
          - `""` if anything went wrong: LLM raised, empty response, or
            response larger than `settings.REPORT_FORMATTER_MAX_CHARS`.

        We deliberately log all failure modes at WARNING level (not
        ERROR) so they're visible to operators but don't trigger
        ops-alerting tooling — a degraded formatter is expected behaviour
        when running against a flaky local LLM.
        """
        try:
            model = self._resolve_model()
            response = await model.ainvoke(prompt)
        except Exception as e:
            logger.warning("format_report: LLM call failed (%s); skipping TL;DR", e)
            return ""

        content = getattr(response, "content", "") or ""
        cleaned = content.strip()
        if not cleaned:
            logger.warning("format_report: LLM returned empty content; skipping TL;DR")
            return ""

        cap = settings.REPORT_FORMATTER_MAX_CHARS
        if len(cleaned) > cap:
            logger.warning(
                "format_report: LLM returned %d chars (cap=%d); skipping TL;DR",
                len(cleaned),
                cap,
            )
            return ""

        return cleaned


def _build_prompt(reviews: list[AgentReview]) -> str:
    """Render the findings list into a tight, structured block for the LLM.

    We pass per-finding facts only (file, line, severity, issue text) —
    NOT free-form summaries — so the LLM has zero room to lean on the
    reviewer's prose for anything other than the items we explicitly
    enumerate. This is the prompt-level twin of the
    `do-not-invent-files` instruction.
    """
    blocks: list[str] = []
    for r in reviews:
        if not r.findings:
            continue
        blocks.append(f"\n[{r.role}] ({r.severity} overall, {len(r.findings)} findings)")
        for f in r.findings:
            file_loc = f.get("file", "?")
            if f.get("line"):
                file_loc = f"{file_loc}:{f['line']}"
            sev = f.get("severity", "info")
            cat = f.get("category") or f.get("package") or ""
            issue = f.get("issue") or f.get("raw") or ""
            cat_part = f" {cat} —" if cat else ""
            blocks.append(f"  - {file_loc} —{cat_part} [{sev}] {issue}")
    findings_block = "\n".join(blocks).strip() or "(no findings)"
    n_roles = sum(1 for r in reviews if r.findings)
    total = sum(len(r.findings) for r in reviews)
    return _PROMPT_TEMPLATE.format(
        total=total,
        n_roles=n_roles,
        findings_block=findings_block,
    )
