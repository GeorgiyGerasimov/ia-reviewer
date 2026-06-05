"""PastContextAgent — RAG node that fetches similar past findings.

Sits between `validate_request` (accept branch) and the security-reviewer
fan-out. The shape mirrors the rest of the agent layer: `run(state)`
returns a partial state update, no I/O outside the injected dependencies.

It does two things:

  1. Build a single embedding for a query string derived from the
     incoming `ReviewRequest`.
  2. For every in-scope role, call `ReviewStore.retrieve_similar` with
     that embedding and the request's `target_url` (= `pr_url` in PR-mode,
     `repo_url` in repo-mode), then write the top-K hits per role into
     `state.past_findings_by_role`.

Graceful no-op when ANY of these is true (RAG never blocks the review):

  * `embedder` is None — operator didn't configure `EMBEDDING_MODEL`.
  * `store` is None — no `DATABASE_URL` configured; nothing to retrieve.
  * `embedder.embed()` returned None — gateway didn't ship `/embeddings`
    or had a transient failure; we don't pollute `past_findings_by_role`
    with stale data.
  * `state.request` is None — defensive; the graph shouldn't reach here
    on the reject path but the field can be missing if state is loaded
    from a broken checkpoint.

`state.request.scope` restricts the role set: `scope=["injection"]` means
we don't burn three SQL queries when only one reviewer will run.
"""

from __future__ import annotations

from typing import Any

from src.graph.state import ALLOWED_SCOPE_ROLES, ReviewState
from src.utils.logger import get_logger

logger = get_logger(__name__)


class PastContextAgent:
    """Embed + retrieve past findings for the security reviewers."""

    def __init__(
        self,
        *,
        embedder: Any | None = None,
        store: Any | None = None,
        top_k: int = 5,
    ):
        self.embedder = embedder
        self.store = store
        self.top_k = top_k

    async def run(self, state: ReviewState) -> dict:
        if state.request is None:
            return {"past_findings_by_role": {}}
        if self.embedder is None or self.store is None:
            # Disabled by configuration — skip silently.
            return {"past_findings_by_role": {}}

        roles = self._roles_in_scope(state)
        if not roles:
            return {"past_findings_by_role": {}}

        target_url = _target_url(state)
        if not target_url:
            logger.warning("past_context: empty target_url; skipping retrieval")
            return {"past_findings_by_role": {}}

        query_text = _build_query_text(state)
        vector = await self.embedder.embed(query_text)
        if vector is None:
            # Embedder is fail-soft — None means "no retrieval this run".
            logger.info("past_context: embedder returned None; skipping retrieval")
            return {"past_findings_by_role": {}}

        by_role: dict[str, list[dict]] = {}
        for role in roles:
            try:
                rows = await self.store.retrieve_similar(
                    target_url=target_url,
                    role=role,
                    query_embedding=vector,
                    k=self.top_k,
                )
            except Exception as e:  # noqa: BLE001 — keep RAG strictly fail-soft
                logger.warning(
                    "past_context: retrieve_similar failed for role=%s: %s", role, e
                )
                rows = []
            by_role[role] = rows
        logger.info(
            "past_context: retrieved %s past findings",
            {r: len(v) for r, v in by_role.items()},
        )
        return {"past_findings_by_role": by_role}

    def _roles_in_scope(self, state: ReviewState) -> list[str]:
        scope = state.request.scope if state.request else []
        if not scope:
            return list(ALLOWED_SCOPE_ROLES)
        # Preserve canonical ordering so retrieval is deterministic.
        return [r for r in ALLOWED_SCOPE_ROLES if r in scope]


# ── helpers ────────────────────────────────────────────────────────────────


def _target_url(state: ReviewState) -> str:
    req = state.request
    if req is None:
        return ""
    if req.mode == "repo":
        return req.repo_url or ""
    return req.pr_url or ""


def _build_query_text(state: ReviewState) -> str:
    """Compose the text we embed to search past findings.

    The embedding is meant to surface findings related to *what we're
    about to review*. Two ingredients buy the most signal for low
    embedding cost:

      * The target URL — ensures embeddings for the same repo cluster.
      * The list of files touched (PR-mode) or in the repo (repo-mode).
        This gives the model a hint of which subsystems are in play
        without dumping the entire diff into the embedding.

    Author and ref are included as hints but not weighted.
    """
    req = state.request
    parts: list[str] = []
    if req.mode == "repo":
        parts.append(f"repo: {req.repo_url}")
        if req.ref:
            parts.append(f"ref: {req.ref}")
        if req.repo_files:
            # Cap so the embed-text doesn't balloon on huge repos.
            file_list = ", ".join(f.path for f in req.repo_files[:40])
            parts.append(f"files: {file_list}")
    else:
        parts.append(f"pr: {req.pr_url}")
        if req.author:
            parts.append(f"author: {req.author}")
        if req.files_changed:
            parts.append(f"files: {', '.join(req.files_changed[:40])}")
    return "\n".join(parts)
