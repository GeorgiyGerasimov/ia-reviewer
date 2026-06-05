"""Persist finalized review state into Postgres + serve list/get queries.

Schema lives in `db/init/03-reviews-schema.sql` (runs on first Postgres
boot). `ReviewStore.ensure_schema` replays the same DDL idempotently at
app startup so tables exist even when Postgres was provisioned before
the feature landed.

The store skips writes silently when `state.thread_id` is empty (no
synthetic PK) — same defensive policy as `CoordinatorAgent._write_repo_report`.

When `DATABASE_URL` is unset, the lifespan wires `app.state.review_store
= None` and `_run_review` / `_run_repo_review` no-op the persistence step.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.graph.state import AgentReview, ReviewState
from src.utils.logger import get_logger

logger = get_logger(__name__)

_SCHEMA_PATH = Path(__file__).parent.parent.parent / "db" / "init" / "03-reviews-schema.sql"

_SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "critical": 3}

_INSERT_REVIEW_SQL = """
INSERT INTO reviews (
    thread_id, mode, target_url, ref, author,
    validation_category, validation_accepted,
    overall_severity, finding_count,
    report_markdown, completed_at, exploit_proposals
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
ON CONFLICT (thread_id) DO UPDATE SET
    mode = EXCLUDED.mode,
    target_url = EXCLUDED.target_url,
    ref = EXCLUDED.ref,
    author = EXCLUDED.author,
    validation_category = EXCLUDED.validation_category,
    validation_accepted = EXCLUDED.validation_accepted,
    overall_severity = EXCLUDED.overall_severity,
    finding_count = EXCLUDED.finding_count,
    report_markdown = EXCLUDED.report_markdown,
    completed_at = EXCLUDED.completed_at,
    exploit_proposals = EXCLUDED.exploit_proposals
"""

_INSERT_FINDINGS_SQL = """
INSERT INTO review_findings (
    thread_id, role, file, line, category, severity, issue, raw
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""

_DELETE_FINDINGS_SQL = "DELETE FROM review_findings WHERE thread_id = $1"

_LIST_SQL = """
SELECT
    thread_id, mode, target_url, ref, author,
    validation_category, validation_accepted,
    overall_severity, finding_count,
    created_at, completed_at
FROM reviews
ORDER BY created_at DESC
LIMIT $1 OFFSET $2
"""

_GET_REVIEW_SQL = """
SELECT
    thread_id, mode, target_url, ref, author,
    validation_category, validation_accepted,
    overall_severity, finding_count,
    report_markdown, exploit_proposals,
    created_at, completed_at
FROM reviews
WHERE thread_id = $1
"""

_GET_FINDINGS_SQL = """
SELECT id, role, file, line, category, severity, issue, raw
FROM review_findings
WHERE thread_id = $1
ORDER BY id
"""


@dataclass
class ReviewStore:
    """Thin asyncpg wrapper. `pool` is an `asyncpg.Pool` in production;
    tests inject a mock with the same `acquire()` context-manager shape."""

    pool: Any

    @classmethod
    async def create(cls, dsn: str) -> "ReviewStore":
        """Open a connection pool and ensure the schema exists.

        Imported lazily so `asyncpg` is only a runtime dep when the app
        is actually pointed at Postgres.

        Registers a JSONB codec so `reviews.exploit_proposals` round-trips
        as Python `list[dict]` instead of a raw JSON string — the
        `/reviews/<id>` endpoint then returns it as a real array,
        not a string the client has to parse a second time.
        """
        import asyncpg

        async def _init_connection(conn):
            await conn.set_type_codec(
                "jsonb",
                encoder=json.dumps,
                decoder=json.loads,
                schema="pg_catalog",
            )

        pool = await asyncpg.create_pool(
            dsn=dsn, min_size=1, max_size=5, init=_init_connection
        )
        store = cls(pool=pool)
        await store.ensure_schema()
        return store

    async def aclose(self) -> None:
        await self.pool.close()

    async def ensure_schema(self) -> None:
        ddl = _SCHEMA_PATH.read_text()
        async with self.pool.acquire() as conn:
            await conn.execute(ddl)

    async def save_review(self, state: ReviewState) -> None:
        """Upsert the review row + replace its findings. Called from
        `_run_review` / `_run_repo_review` after the graph completes.

        Skipped (no-op) if `state.thread_id` is empty — no synthetic PK.
        """
        if not state.thread_id:
            logger.warning("review_store: skipping save — thread_id is empty")
            return

        review_row = _review_row(state)
        finding_rows = _finding_rows(state)

        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(_INSERT_REVIEW_SQL, *review_row)
            await conn.execute(_DELETE_FINDINGS_SQL, state.thread_id)
            if finding_rows:
                await conn.executemany(_INSERT_FINDINGS_SQL, finding_rows)
        logger.info(
            "review_store: saved review thread_id=%s, findings=%d",
            state.thread_id,
            len(finding_rows),
        )

    async def list_reviews(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(_LIST_SQL, limit, offset)
        return [dict(r) for r in rows]

    async def get_review(self, thread_id: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(_GET_REVIEW_SQL, thread_id)
            if not row:
                return None
            findings = await conn.fetch(_GET_FINDINGS_SQL, thread_id)
        return {**dict(row), "findings": [dict(f) for f in findings]}


# ── pure helpers (tested via save_review's SQL assertions) ─────────────


def _review_row(state: ReviewState) -> tuple:
    """Build the positional tuple for the `reviews` INSERT.

    Order matches `_INSERT_REVIEW_SQL` placeholders 1..11.
    """
    req = state.request
    findings = _all_findings(state)
    mode = getattr(req, "mode", "pr")
    target_url = req.pr_url if mode == "pr" else req.repo_url
    # PR-mode doesn't carry a meaningful ref — store NULL so queries can
    # distinguish `WHERE ref IS NULL` for PR rows.
    ref = None if mode == "pr" else (req.ref or "HEAD")
    accepted = getattr(state.validation, "accepted", None)
    category = getattr(state.validation, "category", None)
    return (
        state.thread_id,                         # $1
        mode,                                    # $2
        target_url,                              # $3
        ref,                                     # $4
        req.author or None,                      # $5
        category,                                # $6
        accepted,                                # $7
        _overall_severity(state.agent_reviews),  # $8
        len(findings),                           # $9
        state.final_report or "",                # $10
        None,                                    # $11 completed_at — let app or DB stamp
        _exploit_proposals_json(state),          # $12 JSONB
    )


def _exploit_proposals_json(state: ReviewState) -> list[dict]:
    """Convert `state.exploit_proposals` to list[dict] for the JSONB
    column. The pool-level JSONB codec (registered in `ReviewStore.create`)
    serialises this to wire format on its way out; on the way back in,
    the same codec decodes the column straight to Python list[dict].
    """
    payloads = []
    for ep in state.exploit_proposals or []:
        # Dataclass → dict; safe vs unknown future fields.
        payloads.append({
            "finding_id": ep.finding_id,
            "role": ep.role,
            "severity": ep.severity,
            "status": ep.status,
            "proposal_text": ep.proposal_text,
            "artifact": ep.artifact,
            "confidence": ep.confidence,
        })
    return payloads


def _finding_rows(state: ReviewState) -> list[tuple]:
    """Build the positional rows for `review_findings.executemany`.

    Order: (thread_id, role, file, line, category, severity, issue, raw).
    """
    rows: list[tuple] = []
    for role, f in _all_findings(state):
        rows.append((
            state.thread_id,
            role,
            f.get("file") or None,
            f.get("line") if isinstance(f.get("line"), int) else None,
            f.get("category") or f.get("package") or None,
            _normalize_severity(f.get("severity") or "info"),
            f.get("issue") or f.get("raw") or "",
            # Stash the original finding dict so anything we didn't
            # promote to a column is still recoverable.
            json.dumps(f, default=str),
        ))
    return rows


def _all_findings(state: ReviewState) -> list[tuple[str, dict]]:
    """Yield (role, finding-dict) per finding. Dedupes by role keeping
    the latest AgentReview (last-write-wins, matching `_render_report`)."""
    latest_by_role: dict[str, AgentReview] = {}
    for review in state.agent_reviews:
        latest_by_role[review.role] = review
    pairs: list[tuple[str, dict]] = []
    for role, review in latest_by_role.items():
        for f in review.findings:
            pairs.append((role, f))
    return pairs


def _overall_severity(reviews: list[AgentReview]) -> str:
    """Highest severity across (deduplicated) reviewers."""
    if not reviews:
        return "info"
    latest_by_role: dict[str, AgentReview] = {}
    for r in reviews:
        latest_by_role[r.role] = r
    max_rank = max(
        _SEVERITY_RANK.get(r.severity, 0) for r in latest_by_role.values()
    )
    for label, rank in _SEVERITY_RANK.items():
        if rank == max_rank:
            return label
    return "info"


def _normalize_severity(raw: str) -> str:
    s = (raw or "").strip().lower()
    return s if s in _SEVERITY_RANK else "info"
