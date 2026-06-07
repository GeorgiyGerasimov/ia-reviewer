"""CoordinatorAgent — I/O-only graph nodes for the review pipeline.

Owns the non-LLM nodes that the security reviewers feed into:
  * `aggregate` — runs after the four reviewers finish; calls the
    pure `ReportRenderer` to produce `state.final_report`. No I/O.
  * `publish_report` — repo-mode writes `<reports_dir>/<thread_id>.md`,
    PR-mode also posts a GitHub PR comment with the report body.
  * `notify_rejection` — on the validator-reject branch, posts a
    category-templated rejection notice (PR comment or local file).

Exploit-PoC generation lives outside the graph now (UX rework — see
`src/agents/exploit_proposal.py` and `POST /reviews/{tid}/exploits/{fid}`
in main.py). The `finalize_exploits` node that used to re-render the
report after a sequential interrupt loop is gone; on-demand creation
writes its own sibling .md directly from the endpoint.

Markdown rendering itself lives in `src/agents/report_renderer.py`. This
class deliberately stays thin so the LangGraph wiring is the only place
that knows the graph topology, and the rendering can be unit-tested
without a graph, a tempdir, or a GitHub token.

Re-exports `_render_exploit_section`, `_render_exploit_sibling`, and
`exploit_artifact_filename` for backwards-compat with tests that imported
those names before the ReportRenderer split.
"""

from pathlib import Path

from src.agents.report_renderer import (
    ReportRenderer,
    _render_exploit_section,
    _render_exploit_sibling,
)
from src.graph.state import ReviewState
from src.integrations.github import GitHubClient
from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


# Back-compat alias for callers that imported the function name from
# coordinator before the renderer split (`tests/integration/test_finalize_exploits.py`
# and the embedded JS UI rely on the same filename pattern).
def exploit_artifact_filename(thread_id: str, finding_id: str) -> str:
    return ReportRenderer.exploit_artifact_filename(thread_id, finding_id)


# Re-export so existing tests like `tests/unit/test_render_exploit_save_mode.py`
# and `tests/unit/test_exploit_disclaimer.py` still find these helpers under
# the old import path. New code should import from `report_renderer` directly.
__all__ = [
    "CoordinatorAgent",
    "exploit_artifact_filename",
    "_render_exploit_section",
    "_render_exploit_sibling",
]


class CoordinatorAgent:
    """Owns the non-review nodes of the graph: aggregation, publishing, error handling.

    Kept deliberately thin — rendering is delegated to `ReportRenderer`.
    The three security agents (`dependency`, `injection`, `owasp`) write
    into `state.agent_reviews` concurrently via the `add` reducer; this
    class then asks the renderer for a single Markdown report and posts
    it via the appropriate channel for the request mode.
    """

    def __init__(
        self,
        github: GitHubClient | None = None,
        reports_dir: Path | str | None = None,
        renderer: ReportRenderer | None = None,
    ):
        self.github = github or GitHubClient()
        # Repo-mode publish writes `<reports_dir>/<thread_id>.md`. In PR-mode
        # this is unused. Tests inject a `tmp_path`; production reads from
        # `settings.REPORTS_DIR`.
        self.reports_dir = Path(reports_dir) if reports_dir else Path(settings.REPORTS_DIR)
        # `renderer` injection lets tests / branded deployments swap in a
        # custom renderer subclass (GHE-flavoured Markdown, internal
        # corp template). Default is the stock renderer — zero state,
        # safe to share across coordinators.
        self.renderer = renderer or ReportRenderer()

    async def aclose(self) -> None:
        await self.github.aclose()

    async def aggregate(self, state: ReviewState) -> dict:
        return {
            "final_report": self.renderer.render_review(
                state.agent_reviews,
                state.exploit_proposals,
                request=state.request,
                thread_id=state.thread_id,
            )
        }

    def _render_report(self, reviews, exploit_proposals=None, *, request=None, thread_id: str = "") -> str:
        """Back-compat shim — existing tests called this method on the
        coordinator before the renderer split. New code should call
        `self.renderer.render_review(...)` directly. Keeping this thin
        wrapper keeps the test surface stable across the refactor.
        """
        return self.renderer.render_review(
            reviews,
            exploit_proposals,
            request=request,
            thread_id=thread_id,
        )

    async def notify_rejection(self, state: ReviewState) -> dict:
        """Post a short rejection notice and mark the run completed.

        PR-mode  → top-level comment on the PR (GitHub API).
        Repo-mode → write the same body to `<reports_dir>/<thread_id>.md`;
                    GitHub is left alone (no commit, no issue, no comment).
        """
        if not state.request:
            return {"error": "missing request", "completed": True}

        body = self.renderer.render_rejection(state.validation)

        if state.request.mode == "repo":
            return self._write_repo_report(state, body, error_prefix="notify_rejection")

        try:
            comment_id = await self.github.post_pr_comment(state.request.pr_url, body)
        except Exception as e:
            logger.error("notify_rejection failed for %s: %s", state.request.pr_url, e)
            return {"error": f"notify_rejection failed: {e}", "completed": True}
        return {"pr_comment_id": comment_id, "completed": True}

    async def publish(self, state: ReviewState) -> dict:
        logger.info(
            "publish() entered: mode=%s, thread_id=%r, final_report_len=%d",
            getattr(state.request, "mode", "?"),
            state.thread_id,
            len(state.final_report or ""),
        )
        if not state.request:
            logger.warning("publish: state.request is missing — nothing to publish")
            return {"error": "missing request", "completed": True}
        if not state.final_report:
            logger.warning(
                "publish: state.final_report is empty — aggregate_results did not run "
                "or produced empty output (thread_id=%r)",
                state.thread_id,
            )
            return {"error": "empty final_report", "completed": True}

        if state.request.mode == "repo":
            return self._write_repo_report(state, state.final_report, error_prefix="publish")

        # PR-mode: mirror the report to disk first so the UI report panel
        # (which always reads `/reports/<thread_id>.md`) works the same way
        # in both modes. The GitHub comment is still the primary deliverable.
        disk = self._write_repo_report(state, state.final_report, error_prefix="publish")
        try:
            comment_id = await self.github.post_pr_comment(
                state.request.pr_url, state.final_report
            )
        except Exception as e:
            logger.error("publish failed for %s: %s", state.request.pr_url, e)
            return {"error": f"publish failed: {e}", "completed": True, **disk}
        return {"pr_comment_id": comment_id, "completed": True, **disk}

    def _write_repo_report(self, state: ReviewState, body: str, *, error_prefix: str) -> dict:
        """Write `body` to `<reports_dir>/<thread_id>.md`.

        Used by `publish` (final report), `notify_rejection` (templated
        rejection notice), and `finalize_exploits` (re-render with
        exploits). Returns an update dict with `report_path` so
        downstream code (chat broadcast in main.py) can reference the
        file.

        Skips the write when `state.thread_id` is empty — otherwise
        every such call would clobber the same `unknown.md`.
        """
        if not state.thread_id:
            logger.warning(
                "%s: skipping disk write — state.thread_id is empty",
                error_prefix,
            )
            return {"completed": True}
        try:
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            report_path = self.reports_dir / f"{state.thread_id}.md"
            report_path.write_text(body)
        except Exception as e:
            logger.error(
                "%s failed to write report for %s: %s",
                error_prefix,
                state.thread_id,
                e,
            )
            return {"error": f"{error_prefix} failed: {e}", "completed": True}
        logger.info(
            "%s wrote report: %s (%d bytes)",
            error_prefix,
            report_path.resolve(),
            len(body),
        )
        return {"completed": True, "report_path": str(report_path)}
