"""ReviewStore RAG additions: retrieve_similar + embed_pending.

retrieve_similar(target_url, role, query_embedding, k)
  - Joins review_findings with reviews on thread_id.
  - Filters by `r.target_url = $1 AND rf.role = $2 AND embedding IS NOT NULL`.
  - Orders by cosine distance `embedding <=> $3::vector` ascending.
  - Returns list[dict] with the finding fields + `distance`.
  - Empty query_embedding → [] with no SQL call (defensive).
  - k clamped to >0; non-positive k → [].

embed_pending(embedder, batch_size)
  - SELECT rows where embedding IS NULL (limit batch_size).
  - For each row: build canonical text, call embedder.embed.
  - On success: UPDATE review_findings SET embedding=$1::vector WHERE id=$2.
  - On embedder failure (None): stop the loop, return count so far.
  - Returns the number of rows successfully embedded.

Tests mock asyncpg pool / connection in the same shape as
`tests/unit/test_review_store_sql.py` — pure SQL-shape assertions, no DB.
"""

import pytest

from src.storage.review_store import ReviewStore


@pytest.fixture
def fake_conn(mocker):
    conn = mocker.AsyncMock()
    conn.execute = mocker.AsyncMock(return_value="UPDATE 1")
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


# ── retrieve_similar ──────────────────────────────────────────────────────────


async def test_retrieve_similar_builds_join_and_cosine_order(store, fake_conn):
    fake_conn.fetch.return_value = []

    await store.retrieve_similar(
        target_url="https://github.com/o/r",
        role="injection",
        query_embedding=[0.1, 0.2, 0.3, 0.4],
        k=5,
    )

    fake_conn.fetch.assert_awaited_once()
    sql, *params = fake_conn.fetch.await_args.args
    sql_lower = sql.lower()
    # JOIN through reviews so we can filter by target_url
    assert "review_findings" in sql_lower
    assert "join reviews" in sql_lower or "reviews r" in sql_lower
    # Cosine-distance ORDER BY
    assert "<=>" in sql, "must use cosine distance operator"
    assert "order by" in sql_lower
    # Restrict to populated embeddings
    assert "is not null" in sql_lower
    # Same-repo, same-role filter
    assert "target_url" in sql_lower
    assert "role" in sql_lower


async def test_retrieve_similar_passes_target_url_role_and_vector_string(store, fake_conn):
    """The vector must be serialised as a pgvector-format string `'[a,b,c]'`
    because asyncpg doesn't know the `vector` type natively."""
    fake_conn.fetch.return_value = []

    await store.retrieve_similar(
        target_url="https://github.com/o/r",
        role="injection",
        query_embedding=[0.5, -0.25, 0.0, 1.0],
        k=3,
    )

    _sql, *params = fake_conn.fetch.await_args.args
    # The vector serialisation must look like '[0.5,-0.25,0.0,1.0]' (no spaces
    # before the bracket; PG accepts spaces but the test is on shape).
    vector_param = next(
        (p for p in params if isinstance(p, str) and p.startswith("[") and p.endswith("]")),
        None,
    )
    assert vector_param is not None, f"expected vector string in params, got {params!r}"
    # Each component round-trips faithfully
    assert "0.5" in vector_param
    assert "-0.25" in vector_param
    assert "1" in vector_param  # 1.0 or "1.00000000"
    # Other params present
    assert "https://github.com/o/r" in params
    assert "injection" in params
    assert 3 in params


async def test_retrieve_similar_returns_row_dicts_with_distance(store, fake_conn):
    fake_conn.fetch.return_value = [
        {
            "id": 7,
            "thread_id": "tid-7",
            "role": "injection",
            "file": "src/auth.py",
            "line": 42,
            "category": "sqli",
            "severity": "major",
            "issue": "SQL via f-string",
            "distance": 0.12,
        },
        {
            "id": 12,
            "thread_id": "tid-12",
            "role": "injection",
            "file": "src/users.py",
            "line": 10,
            "category": "cmd",
            "severity": "minor",
            "issue": "shell=True call",
            "distance": 0.34,
        },
    ]

    out = await store.retrieve_similar(
        target_url="https://github.com/o/r",
        role="injection",
        query_embedding=[0.1, 0.2],
        k=10,
    )

    assert len(out) == 2
    assert out[0]["id"] == 7
    assert out[0]["distance"] == 0.12
    assert out[0]["issue"] == "SQL via f-string"
    assert out[1]["distance"] == 0.34


async def test_retrieve_similar_with_empty_query_embedding_returns_empty(store, fake_conn):
    """Defensive: no embedding → no SQL call, just []. RAG-disabled paths
    pass query_embedding=None to mean "skip retrieval"."""
    out = await store.retrieve_similar(
        target_url="x",
        role="injection",
        query_embedding=[],
        k=5,
    )

    assert out == []
    fake_conn.fetch.assert_not_called()


async def test_retrieve_similar_with_none_query_embedding_returns_empty(store, fake_conn):
    out = await store.retrieve_similar(
        target_url="x",
        role="injection",
        query_embedding=None,
        k=5,
    )

    assert out == []
    fake_conn.fetch.assert_not_called()


async def test_retrieve_similar_with_non_positive_k_returns_empty(store, fake_conn):
    """`k <= 0` is a defensive shortcut: callers that hit MAX_K-=N can land
    on 0; instead of throwing or surprising LIMIT 0 SQL, return []."""
    out = await store.retrieve_similar(
        target_url="x",
        role="injection",
        query_embedding=[0.1, 0.2],
        k=0,
    )

    assert out == []
    fake_conn.fetch.assert_not_called()


# ── embed_pending ─────────────────────────────────────────────────────────────


async def test_embed_pending_selects_null_embeddings_and_updates_each(store, fake_conn, mocker):
    """Backfill loop: SELECT rows w/ NULL embedding, embed each, UPDATE."""
    fake_conn.fetch.return_value = [
        {"id": 1, "role": "injection", "file": "a.py", "category": "sqli", "issue": "issue-a"},
        {"id": 2, "role": "injection", "file": "b.py", "category": "cmd", "issue": "issue-b"},
    ]
    embedder = mocker.AsyncMock()
    # Return distinct embeddings per call so we can assert which row got which.
    embedder.embed = mocker.AsyncMock(side_effect=[[0.1, 0.2], [0.3, 0.4]])

    count = await store.embed_pending(embedder, batch_size=50)

    assert count == 2
    assert embedder.embed.await_count == 2

    # The SELECT must filter NULL embedding and limit by batch_size.
    select_call = next(
        c for c in fake_conn.fetch.call_args_list if "review_findings" in c.args[0]
    )
    sql = select_call.args[0].lower()
    assert "is null" in sql
    assert "limit" in sql

    # Each row got UPDATEd with its embedding (positional id).
    update_calls = [
        c for c in fake_conn.execute.call_args_list
        if "update" in c.args[0].lower() and "embedding" in c.args[0].lower()
    ]
    assert len(update_calls) == 2
    # Each update must carry the row id as a parameter and a vector-string.
    ids_updated = set()
    for c in update_calls:
        params = c.args[1:]
        ids_updated.update(p for p in params if isinstance(p, int))
        assert any(
            isinstance(p, str) and p.startswith("[") and p.endswith("]") for p in params
        ), f"expected vector-string param in update; got {params!r}"
    assert ids_updated == {1, 2}


async def test_embed_pending_stops_on_first_embedder_failure(store, fake_conn, mocker):
    """Embedder.None means gateway is misbehaving — stop the loop, don't
    burn the remaining rows. Returns the count of rows that succeeded."""
    fake_conn.fetch.return_value = [
        {"id": 1, "role": "injection", "file": "a.py", "category": "sqli", "issue": "x"},
        {"id": 2, "role": "injection", "file": "b.py", "category": "sqli", "issue": "y"},
        {"id": 3, "role": "injection", "file": "c.py", "category": "sqli", "issue": "z"},
    ]
    embedder = mocker.AsyncMock()
    embedder.embed = mocker.AsyncMock(side_effect=[[0.1, 0.2], None, [0.5, 0.6]])

    count = await store.embed_pending(embedder)

    # First row succeeded, second failed → stop.
    assert count == 1
    # Embedder called twice (the failing one), third row never reached.
    assert embedder.embed.await_count == 2
    update_calls = [
        c for c in fake_conn.execute.call_args_list
        if "update" in c.args[0].lower()
    ]
    assert len(update_calls) == 1


async def test_embed_pending_with_no_pending_rows_is_a_noop(store, fake_conn, mocker):
    fake_conn.fetch.return_value = []
    embedder = mocker.AsyncMock()
    embedder.embed = mocker.AsyncMock()

    count = await store.embed_pending(embedder)

    assert count == 0
    embedder.embed.assert_not_awaited()


async def test_embed_pending_skips_rows_with_empty_text(store, fake_conn, mocker):
    """Defensive: a finding with neither issue nor category nor file shouldn't
    waste an embedding call. We expect that row to be SKIPPED (no embedder
    call, no update) — but the loop continues to the next row.
    """
    fake_conn.fetch.return_value = [
        {"id": 1, "role": "injection", "file": "", "category": "", "issue": ""},
        {"id": 2, "role": "injection", "file": "a.py", "category": "sqli", "issue": "real text"},
    ]
    embedder = mocker.AsyncMock()
    embedder.embed = mocker.AsyncMock(return_value=[0.1, 0.2])

    count = await store.embed_pending(embedder)

    assert count == 1
    # Only the row with real text triggered an embed call.
    assert embedder.embed.await_count == 1
