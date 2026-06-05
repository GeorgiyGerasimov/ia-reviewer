"""`GET /reviews?limit=&offset=` must validate query params, clamp
extreme values, and respond 400 on garbage instead of crashing the
event loop with `ValueError`.

Review finding PR2.2:
  * `int(request.query_params.get("limit"))` raises 500 on non-numeric.
  * No upper bound — `?limit=9999999999` would issue an unbounded
    Postgres `LIMIT` which (on a populated table) consumes memory at
    server cost.

After this PR:
  * Non-numeric / negative → 400 with a clear error.
  * `limit` clamped to a hard cap (`MAX_REVIEWS_PAGE_SIZE = 200`).
  * `offset` clamped to ≥ 0.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from main import create_test_app


class _FakeStore:
    """Records the `(limit, offset)` actually passed to the store so
    we can assert on the clamp behaviour."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def list_reviews(self, *, limit: int, offset: int) -> list[Any]:
        self.calls.append((limit, offset))
        return []


@pytest.fixture
def app(mocker):
    a = create_test_app(graph=mocker.AsyncMock(), github=mocker.AsyncMock())
    a.state.review_store = _FakeStore()
    return a


def test_default_limit_offset_pass_through(app):
    """No query → store sees (limit=50, offset=0). The 50 default is
    unchanged from the original behaviour."""
    client = TestClient(app)
    r = client.get("/reviews")
    assert r.status_code == 200
    assert app.state.review_store.calls == [(50, 0)]


def test_explicit_limit_offset_pass_through(app):
    client = TestClient(app)
    r = client.get("/reviews?limit=10&offset=20")
    assert r.status_code == 200
    assert app.state.review_store.calls == [(10, 20)]


def test_non_numeric_limit_returns_400(app):
    client = TestClient(app)
    r = client.get("/reviews?limit=abc")
    assert r.status_code == 400
    body = r.json()
    assert "limit" in str(body).lower(), body


def test_non_numeric_offset_returns_400(app):
    client = TestClient(app)
    r = client.get("/reviews?offset=xyz")
    assert r.status_code == 400


def test_negative_limit_returns_400(app):
    client = TestClient(app)
    r = client.get("/reviews?limit=-5")
    assert r.status_code == 400


def test_negative_offset_returns_400(app):
    client = TestClient(app)
    r = client.get("/reviews?offset=-1")
    assert r.status_code == 400


def test_zero_limit_returns_400(app):
    """`limit=0` is logically a no-op query but semantically usually a
    typo for `1`. Reject explicitly so the user sees the mistake."""
    client = TestClient(app)
    r = client.get("/reviews?limit=0")
    assert r.status_code == 400


def test_huge_limit_clamps_to_max(app):
    """`?limit=99999999` clamps to the hard cap (200) instead of
    forwarding to the store and exhausting memory."""
    client = TestClient(app)
    r = client.get("/reviews?limit=99999999")
    assert r.status_code == 200
    [(limit_seen, _)] = app.state.review_store.calls
    assert limit_seen == 200, f"limit must clamp to 200, got {limit_seen}"


def test_store_missing_returns_empty_list(app):
    """Existing contract: no store wired → `[]` not 500. Validation
    must not break this path."""
    app.state.review_store = None
    client = TestClient(app)
    r = client.get("/reviews?limit=10")
    assert r.status_code == 200
    assert r.json() == []
