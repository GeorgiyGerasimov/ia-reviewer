"""ReviewStore persists finalized review state into Postgres.

This test mocks `asyncpg.Pool` / `Connection` to assert on the SQL shape
without standing up a real DB. Lives under tests/unit/ because there's
no real DB I/O.
"""

import pytest

from src.graph.state import (
    AgentReview,
    RepoFile,
    ReviewRequest,
    ReviewState,
    ValidationVerdict,
)
from src.storage.review_store import ReviewStore


@pytest.fixture
def fake_conn(mocker):
    """A minimally-mocked asyncpg.Connection: execute / executemany /
    transaction() context manager."""
    conn = mocker.AsyncMock()
    conn.execute = mocker.AsyncMock(return_value="INSERT 0 1")
    conn.executemany = mocker.AsyncMock(return_value=None)
    conn.fetch = mocker.AsyncMock(return_value=[])
    conn.fetchrow = mocker.AsyncMock(return_value=None)
    tx_cm = mocker.MagicMock()
    tx_cm.__aenter__ = mocker.AsyncMock(return_value=None)
    tx_cm.__aexit__ = mocker.AsyncMock(return_value=False)
    conn.transaction = mocker.MagicMock(return_value=tx_cm)
    return conn


@pytest.fixture
def fake_pool(mocker, fake_conn):
    pool = mocker.MagicMock()
    acquire_cm = mocker.MagicMock()
    acquire_cm.__aenter__ = mocker.AsyncMock(return_value=fake_conn)
    acquire_cm.__aexit__ = mocker.AsyncMock(return_value=False)
    pool.acquire = mocker.MagicMock(return_value=acquire_cm)
    return pool


@pytest.fixture
def store(fake_pool):
    return ReviewStore(pool=fake_pool)


def _repo_state(**overrides) -> ReviewState:
    base = dict(
        thread_id="11111111-1111-4111-8111-111111111111",
        request=ReviewRequest(
            mode="repo",
            repo_url="https://github.com/o/r",
            ref="main",
            author="bot",
            repo_files=[RepoFile(path="x.py", content="", size=10)],
        ),
        validation=ValidationVerdict(accepted=True, category="accepted", reason="ok"),
        agent_reviews=[
            AgentReview(
                agent_name="DependencyReviewer",
                role="dependency",
                findings=[
                    {"file": "package.json", "issue": "unmaintained pkg", "severity": "minor"}
                ],
                summary="one minor finding",
                severity="minor",
                passed=True,
            ),
        ],
        final_report="## Security review\n\n…",
    )
    base.update(overrides)
    return ReviewState(**base)


async def test_save_review_inserts_one_row_and_findings_for_repo_mode(store, fake_conn):
    """Repo-mode review: one row in `reviews`, one row in `review_findings`,
    inside a single transaction. Args must match the table schema."""
    await store.save_review(_repo_state())

    # INSERT INTO reviews fired once with the right columns.
    insert_review = next(
        c for c in fake_conn.execute.call_args_list if "INTO reviews" in c.args[0]
    )
    sql, *params = insert_review.args
    assert "ON CONFLICT (thread_id) DO UPDATE" in sql, (
        "save_review must be idempotent — re-saves should UPSERT"
    )
    # Positional params for the reviews INSERT (in our SQL order)
    assert params[0] == "11111111-1111-4111-8111-111111111111"  # thread_id
    assert "repo" in params, f"expected mode='repo' in params: {params!r}"
    assert "https://github.com/o/r" in params

    # review_findings insert(many)
    insertmany_findings = next(
        c for c in fake_conn.executemany.call_args_list if "review_findings" in c.args[0]
    )
    findings_sql, rows = insertmany_findings.args
    assert "INTO review_findings" in findings_sql
    assert len(rows) == 1
    row = rows[0]
    # Row columns: (thread_id, role, file, line, category, severity, issue, raw)
    assert row[0] == "11111111-1111-4111-8111-111111111111"
    assert row[1] == "dependency"
    assert row[2] == "package.json"
    assert "unmaintained pkg" in row[6]  # issue text


async def test_save_review_handles_pr_mode_with_null_ref(store, fake_conn):
    """PR-mode: `ref` column must be NULL, `target_url` = pr_url."""
    state = ReviewState(
        thread_id="22222222-2222-4222-8222-222222222222",
        request=ReviewRequest(
            mode="pr",
            pr_url="https://github.com/o/r/pull/42",
            diff="diff …",
            files_changed=["x.py"],
            author="dev",
        ),
        validation=ValidationVerdict(accepted=True, category="accepted", reason="ok"),
        agent_reviews=[],
        final_report="## Security review\n\n…",
    )

    await store.save_review(state)

    insert_review = next(
        c for c in fake_conn.execute.call_args_list if "INTO reviews" in c.args[0]
    )
    params = insert_review.args[1:]
    # Find target_url and ref positions: target_url should be the pr_url,
    # and ref should be None (PR-mode doesn't carry a ref).
    assert "https://github.com/o/r/pull/42" in params
    # The PR-mode insert must pass ref=None somewhere.
    assert None in params, f"PR-mode must pass ref=None; params={params!r}"


async def test_save_review_with_no_thread_id_is_a_noop(store, fake_conn):
    """Defensive: if state.thread_id is empty we have no PK to write under —
    skip silently rather than emit a row with a synthetic id."""
    state = _repo_state(thread_id="")

    await store.save_review(state)

    fake_conn.execute.assert_not_called()
    fake_conn.executemany.assert_not_called()


async def test_list_reviews_orders_by_created_at_desc(store, fake_conn):
    """`list_reviews(limit, offset)` selects from `reviews` ordered by
    created_at desc; `report_markdown` is NOT in the projected columns
    (it's heavy; only fetched in get_review)."""
    fake_conn.fetch.return_value = [
        {
            "thread_id": "aaa",
            "mode": "repo",
            "target_url": "https://github.com/a/b",
            "ref": "main",
            "author": "bot",
            "overall_severity": "minor",
            "finding_count": 2,
            "validation_category": "accepted",
            "validation_accepted": True,
            "created_at": None,
            "completed_at": None,
        }
    ]

    rows = await store.list_reviews(limit=10, offset=0)

    fake_conn.fetch.assert_awaited_once()
    sql, *params = fake_conn.fetch.await_args.args
    assert "FROM reviews" in sql
    assert "ORDER BY created_at DESC" in sql
    assert "report_markdown" not in sql.lower(), (
        "list view must omit the heavy markdown column"
    )
    assert params == [10, 0]
    assert rows[0]["thread_id"] == "aaa"


async def test_get_review_returns_none_when_missing(store, fake_conn):
    fake_conn.fetchrow.return_value = None
    result = await store.get_review("does-not-exist")
    assert result is None


async def test_get_review_includes_findings_list(store, fake_conn):
    fake_conn.fetchrow.return_value = {
        "thread_id": "tid",
        "mode": "repo",
        "target_url": "https://github.com/o/r",
        "report_markdown": "## …",
    }
    fake_conn.fetch.return_value = [
        {"role": "dependency", "file": "package.json", "issue": "x", "severity": "minor"},
        {"role": "owasp", "file": "Dockerfile", "issue": "y", "severity": "major"},
    ]

    result = await store.get_review("tid")

    assert result is not None
    assert result["thread_id"] == "tid"
    assert len(result["findings"]) == 2
    roles = [f["role"] for f in result["findings"]]
    assert roles == ["dependency", "owasp"]
