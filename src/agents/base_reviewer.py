"""
Base class for security review agents (dependency, injection, owasp).
Do NOT use for coordinators or report generators — they live in their own modules.

Subclasses set `role`, `prompt_template`, and `PATH_PATTERNS` (fnmatch-style
basenames used by repo-mode review to filter which files this reviewer
processes). The base machinery handles two modes:

  PR-mode   — one LLM call against the unified diff (existing behavior).
  Repo-mode — for each repo file whose basename matches `PATH_PATTERNS`,
              fetch its content via the injected GitHubClient and run one
              LLM call per file. Files above `settings.MAX_FILES_PER_AGENT`
              are truncated with an explicit note in the AgentReview.summary.

In both modes `run` returns a partial state update
`{"agent_reviews": [one_review]}` that LangGraph's `add` reducer concatenates
to enable parallel fan-out.
"""

import asyncio
import fnmatch
import json
import re
from pathlib import Path

from src.graph.state import AgentReview, RepoFile, ReviewState
from src.integrations.github import GitHubClient
from src.models.factory import ModelFactory
from src.utils.config import settings
from src.utils.llm_parsing import strip_thinking_trace
from src.utils.logger import get_logger

logger = get_logger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

VALID_SEVERITIES = ("critical", "major", "minor", "info")
BLOCKING_SEVERITIES = frozenset({"critical", "major"})
_SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "critical": 3}

_SEVERITY_SYNONYMS = {
    "critical": "critical",
    "major": "major",
    "minor": "minor",
    "info": "info",
    "error": "critical",
    "blocker": "critical",
    "high": "major",
    "warning": "minor",
    "low": "minor",
    "note": "info",
    "notice": "info",
}


def normalize_severity(raw) -> str:
    if not isinstance(raw, str):
        return "info"
    return _SEVERITY_SYNONYMS.get(raw.strip().lower(), "info")


def _read_snapshot_file(snapshot_root: Path, rel_path: str) -> str:
    """Best-effort read of a file inside the cloned snapshot.

    Returns "" on any error (missing file, decode error, permission, OR
    path-traversal attempt). The caller treats empty content as "skip
    this file" so a single unreadable file never aborts the whole pass.

    Defence-in-depth: `rel_path` ORIGINATES from `list_repo_files` which
    is safe today, but `RepoFile.path` lives in `ReviewState` and gets
    checkpointed; any future code path that accepts external state must
    NOT be able to escape the snapshot via `..` or symlink. We resolve()
    the full path and verify it still sits under `snapshot_root.resolve()`.
    Failed check → empty string (silent skip), matching the rest of the
    error-handling contract.
    """
    full = snapshot_root / rel_path
    try:
        resolved = full.resolve()
        root_resolved = snapshot_root.resolve()
        if not resolved.is_relative_to(root_resolved):
            logger.warning(
                "snapshot read refused: %r resolves outside snapshot root %r",
                rel_path,
                str(root_resolved),
            )
            return ""
        return resolved.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


class BaseReviewer:
    role: str = ""
    description: str = ""
    prompt_template: str = ""
    # fnmatch-style patterns matched against the file's basename (not the
    # full path), so "*.py" hits any python file regardless of directory.
    # Empty tuple = repo-mode disabled for this reviewer.
    PATH_PATTERNS: tuple[str, ...] = ()

    def __init__(self, model_name: str | None = None, github: GitHubClient | None = None):
        self.model_name = model_name or self._default_model()
        self.model = ModelFactory.get(self.model_name)
        # Optional in PR-mode (reviewers don't touch GitHub directly there);
        # required in repo-mode for per-file content fetching.
        self.github = github

    def _default_model(self) -> str:
        return "claude-sonnet-4-6"

    def _skip_for_scope(self, state: ReviewState) -> bool:
        scope = state.request.scope if state.request else []
        return bool(scope) and self.role not in scope

    @classmethod
    def _matches_path(cls, path: str) -> bool:
        """True iff `path`'s basename matches any of `PATH_PATTERNS`."""
        name = path.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatch(name, p) for p in cls.PATH_PATTERNS)

    async def run(self, state: ReviewState) -> dict:
        if self._skip_for_scope(state):
            return {}
        if state.request is not None and state.request.mode == "repo":
            return await self._run_repo(state)
        return await self._run_pr(state)

    async def _run_pr(self, state: ReviewState) -> dict:
        """Single-LLM-call review against a unified diff (PR-mode).

        Shared by every reviewer regardless of which repo-mode strategy
        the subclass picked — PR-mode is small enough that one LLM call
        against the whole diff is fine, and there's only one shape of
        finding-list to parse out of the response.
        """
        prompt = self.prompt_template.format(context=self._build_context(state))
        response = await self.model.ainvoke(prompt)
        parsed = self._parse_response(response.content)

        review = AgentReview(
            agent_name=self.__class__.__name__,
            role=self.role,
            findings=parsed["findings"],
            summary=parsed["summary"],
            severity=parsed["severity"],
            passed=parsed["passed"],
        )
        return {"agent_reviews": [review]}

    async def _run_repo(self, state: ReviewState) -> dict:
        """Repo-mode entry-point — MUST be implemented by a subclass.

        `BaseReviewer` itself is agnostic about HOW repo-mode works
        because the two real strategies differ enough that sharing a
        body did more harm than good:

          * `LLMPerFileReviewer` runs one LLM call per matched source
            file (Injection, OWASP, anything where the model reads the
            actual code).
          * `ScriptedScannerReviewer` runs a deterministic scan and
            optionally hands the result to ONE LLM for a summary
            (Dependency via OSV.dev, future license-scan, secret-scan).

        Pick the right base for your reviewer. Subclassing `BaseReviewer`
        directly and forgetting to override `_run_repo` would silently
        skip all of repo-mode — raising here makes the omission loud.
        """
        raise NotImplementedError(
            f"{type(self).__name__} must extend either LLMPerFileReviewer or "
            f"ScriptedScannerReviewer and implement `_run_repo` to participate "
            f"in repo-mode reviews."
        )

    def _build_context(self, state: ReviewState) -> str:
        """PR-mode context: diff + metadata + optional past-findings splice
        + optional human-clarification."""
        req = state.request
        parts = [
            f"PR: {req.pr_url}",
            f"Author: {req.author}",
            f"Files changed: {', '.join(req.files_changed)}",
            "",
            f"Diff:\n{req.diff}",
        ]
        past_block = self._render_past_findings(state)
        if past_block:
            parts.append("")
            parts.append(past_block)
        if state.extra_context:
            # Phase B — splice in the human's clarification from the previous
            # pass so the reviewer's second look has the missing context.
            parts.append("")
            parts.append(f"Human clarification from previous pass:\n{state.extra_context}")
        return "\n".join(parts)

    def _build_repo_file_context(self, state: ReviewState, repo_file: RepoFile, content: str) -> str:
        """Repo-mode per-file context: single file's full content + metadata
        + optional past-findings splice + optional human-clarification."""
        req = state.request
        parts = [
            f"Repository: {req.repo_url}@{req.ref}",
            f"File: {repo_file.path} ({repo_file.size} bytes)",
            "",
            f"Content:\n{content}",
        ]
        past_block = self._render_past_findings(state)
        if past_block:
            parts.append("")
            parts.append(past_block)
        if state.extra_context:
            parts.append("")
            parts.append(f"Human clarification from previous pass:\n{state.extra_context}")
        return "\n".join(parts)

    def _render_past_findings(self, state: ReviewState) -> str:
        """RAG splice — render this reviewer's slice of past findings.

        Returns "" when there's nothing to show (no RAG, no past
        findings for this role) so the caller can omit the block
        without noise in the prompt. Each past finding is one bullet
        line carrying severity + file location + issue text — enough
        for the model to recognise a duplicate/regression without
        bloating the prompt.

        Called from both PR-mode and repo-mode context builders.
        """
        by_role = state.past_findings_by_role or {}
        my_past = by_role.get(self.role) or []
        if not my_past:
            return ""

        lines = ["Previous findings on this repo (for context — they may already be fixed or unfixed):"]
        for finding in my_past:
            location = (finding.get("file") or "").strip()
            if location and finding.get("line"):
                location = f"{location}:{finding['line']}"
            severity = finding.get("severity") or "info"
            category = finding.get("category") or ""
            issue = (finding.get("issue") or "").strip() or "(no description)"
            head_parts = [p for p in (location, category, f"[{severity}]") if p]
            head = " — ".join(head_parts)
            lines.append(f"- {head} {issue}".rstrip())
        return "\n".join(lines)

    def _parse_response(self, content: str) -> dict:
        # Hybrid-thinking models (Qwen3.6-27B, DeepSeek R1, o1-style)
        # prepend a chain-of-thought trace fenced by `</think>` before
        # the actual JSON. Strip that BEFORE the JSON-block regex runs —
        # otherwise the long prose either chokes `json.loads` (raw path)
        # or hides a real ```json fence inside the thinking. See
        # tests/unit/test_base_reviewer_thinking.py for the contract
        # and `docs/observed-quality-cases/` for the empirical anchor
        # (Qwen3.6-27B sends ~95% of tokens as thinking trace).
        content = strip_thinking_trace(content)
        match = _JSON_BLOCK_RE.search(content)
        payload = match.group(1) if match else content.strip()
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return {
                "findings": [{"raw": content}],
                "summary": content,
                "severity": "info",
                "passed": True,
            }

        severity = normalize_severity(data.get("severity"))
        return {
            "findings": data.get("findings", []),
            "summary": data.get("summary", ""),
            "severity": severity,
            "passed": severity not in BLOCKING_SEVERITIES,
        }


class LLMPerFileReviewer(BaseReviewer):
    """Repo-mode strategy: one LLM call per matched source file.

    Used by `InjectionReviewer` and `OWASPTop10Reviewer` — both need
    the model to read the actual code (not just a list of CVE ids), so
    running it once per file is the only way to get useful findings.
    `DependencyReviewer` does NOT extend this — its repo-mode is a
    scripted OSV.dev scan, not an LLM loop. See `ScriptedScannerReviewer`.
    """

    async def _run_repo(self, state: ReviewState) -> dict:
        """Per-file LLM iteration over `state.request.repo_files`.

        Filters by `PATH_PATTERNS`, caps at `settings.MAX_FILES_PER_AGENT`,
        reads each file directly from the local snapshot directory cloned
        by `_run_repo_review`, runs one LLM call per file, and aggregates
        findings into a single AgentReview. Truncation (cap exceeded) is
        recorded in the summary so the report surfaces partial coverage
        explicitly.
        """
        request = state.request
        snapshot_root = Path(request.snapshot_dir) if request.snapshot_dir else None
        if snapshot_root is None or not snapshot_root.exists():
            logger.warning(
                "%s: snapshot_dir missing in repo-mode; skipping (got %r)",
                self.role,
                request.snapshot_dir,
            )
            return {}

        matched = [f for f in request.repo_files if self._matches_path(f.path)]
        cap = settings.MAX_FILES_PER_AGENT
        truncated_count = max(0, len(matched) - cap)
        matched = matched[:cap]
        if truncated_count:
            logger.warning(
                "%s: truncating repo-mode scan: %d files over cap %d",
                self.role,
                truncated_count,
                cap,
            )

        # Per-file LLM calls are independent — the model sees only ONE
        # file's contents per prompt, so parallelising them costs nothing
        # on quality and cuts wall-clock linearly until the gateway
        # bottlenecks. `MAX_CONCURRENT_FILES_PER_AGENT=1` collapses to
        # sequential (back-compat). See settings docstring for tuning.
        cap_concurrent = max(1, settings.MAX_CONCURRENT_FILES_PER_AGENT)
        semaphore = asyncio.Semaphore(cap_concurrent)

        async def _process_one(repo_file: RepoFile):
            """Run one file through read → prompt → LLM → parse.
            Returns None if the file is empty (we skip empties), or the
            parsed dict tagged with `file_path`. Exceptions are
            converted to None at the gather() level so one failing call
            does not sink the whole pass."""
            async with semaphore:
                content = _read_snapshot_file(snapshot_root, repo_file.path)
                if not content:
                    return None
                prompt = self.prompt_template.format(
                    context=self._build_repo_file_context(state, repo_file, content)
                )
                response = await self.model.ainvoke(prompt)
                parsed = self._parse_response(response.content)
                # Tag findings with the file path if the model didn't.
                for finding in parsed["findings"]:
                    finding.setdefault("file", repo_file.path)
                return {
                    "file_path": repo_file.path,
                    "parsed": parsed,
                }

        # gather(return_exceptions=True) → one bad file does not sink the
        # whole pass; the failing index slot becomes an exception object
        # we filter out below. Order is preserved from `matched`, so the
        # aggregation that follows produces stable output regardless of
        # which underlying call finished first.
        per_file_results = await asyncio.gather(
            *(_process_one(rf) for rf in matched),
            return_exceptions=True,
        )

        all_findings: list[dict] = []
        per_file_summaries: list[str] = []
        highest_severity = "info"

        for repo_file, outcome in zip(matched, per_file_results, strict=True):
            if isinstance(outcome, BaseException):
                logger.warning(
                    "%s: per-file LLM call failed for %s: %s",
                    self.role,
                    repo_file.path,
                    outcome,
                )
                continue
            if outcome is None:
                # empty file or read failure — already logged inside _read_snapshot_file
                continue
            parsed = outcome["parsed"]
            all_findings.extend(parsed["findings"])
            if parsed["summary"] and parsed["findings"]:
                per_file_summaries.append(f"{repo_file.path}: {parsed['summary']}")
            if _SEVERITY_RANK[parsed["severity"]] > _SEVERITY_RANK[highest_severity]:
                highest_severity = parsed["severity"]

        summary_parts: list[str] = []
        if truncated_count:
            summary_parts.append(
                f"truncated: {truncated_count} files over cap of {cap} were not scanned"
            )
        if per_file_summaries:
            summary_parts.append(" | ".join(per_file_summaries[:5]))
        else:
            summary_parts.append("no findings")
        summary = ". ".join(summary_parts)

        review = AgentReview(
            agent_name=self.__class__.__name__,
            role=self.role,
            findings=all_findings,
            summary=summary,
            severity=highest_severity,
            passed=highest_severity not in BLOCKING_SEVERITIES,
        )
        return {"agent_reviews": [review]}


class ScriptedScannerReviewer(BaseReviewer):
    """Repo-mode strategy: subclass runs a deterministic scan and
    optionally hands the results to ONE LLM call for a narrative
    summary.

    Used by `DependencyReviewer` (parse manifests + OSV.dev → single
    summary LLM). Reusable pattern for future scanners (license-check,
    secret-scan, IaC misconfig — whenever a deterministic tool is the
    right primary source and the LLM only writes the narrative).

    `_run_repo` is NOT implemented here — subclasses MUST override.
    `BaseReviewer._run_repo` raises a clear NotImplementedError if you
    forget; subclassing this class explicitly documents the intent.
    """
    # Intentionally inherits the raising `_run_repo` from `BaseReviewer`.
    # Subclasses MUST override it. This class is a marker / documentation
    # device — sister to `LLMPerFileReviewer`.
