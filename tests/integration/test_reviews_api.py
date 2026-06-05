"""HTTP endpoints for browsing persisted reviews.

`GET /reviews` lists the most recent reviews (no markdown body).
`GET /reviews/{thread_id}` returns one review with embedded findings.
Both gracefully no-op (200 [] / 404) when `app.state.review_store` is None
— the same degraded-but-functional mode we use for missing checkpointer.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from main import create_test_app


@pytest.fixture
def app(mocker):
    return create_test_app(graph=mocker.AsyncMock(), github=mocker.AsyncMock())


@pytest.fixture
async def http_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_get_reviews_returns_empty_when_store_disabled(http_client, app):
    """No DATABASE_URL → no store wired → endpoint replies with [] (NOT 503).
    The UI should be able to render the section as empty rather than error."""
    app.state.review_store = None

    response = await http_client.get("/reviews")

    assert response.status_code == 200
    assert response.json() == []


async def test_get_reviews_returns_list_from_store(http_client, app, mocker):
    fake_store = mocker.AsyncMock()
    fake_store.list_reviews = mocker.AsyncMock(
        return_value=[
            {"thread_id": "a", "mode": "repo", "target_url": "https://github.com/x/y"},
            {"thread_id": "b", "mode": "pr", "target_url": "https://github.com/x/y/pull/1"},
        ]
    )
    app.state.review_store = fake_store

    response = await http_client.get("/reviews?limit=20&offset=0")

    assert response.status_code == 200
    body = response.json()
    assert [r["thread_id"] for r in body] == ["a", "b"]
    fake_store.list_reviews.assert_awaited_once_with(limit=20, offset=0)


async def test_get_review_by_thread_id_returns_404_when_missing(http_client, app, mocker):
    fake_store = mocker.AsyncMock()
    fake_store.get_review = mocker.AsyncMock(return_value=None)
    app.state.review_store = fake_store

    response = await http_client.get("/reviews/nonexistent")

    assert response.status_code == 404


async def test_get_review_by_thread_id_includes_findings(http_client, app, mocker):
    fake_store = mocker.AsyncMock()
    fake_store.get_review = mocker.AsyncMock(
        return_value={
            "thread_id": "tid-1",
            "mode": "repo",
            "target_url": "https://github.com/o/r",
            "report_markdown": "## Security review\n\n…",
            "findings": [
                {"role": "owasp", "file": "Dockerfile", "severity": "major", "issue": "x"},
            ],
            "exploit_proposals": [],
        }
    )
    app.state.review_store = fake_store

    response = await http_client.get("/reviews/tid-1")

    assert response.status_code == 200
    body = response.json()
    assert body["thread_id"] == "tid-1"
    assert body["findings"][0]["role"] == "owasp"
    fake_store.get_review.assert_awaited_once_with("tid-1")


async def test_get_review_by_thread_id_includes_exploit_proposals(http_client, app, mocker):
    """The /reviews/<id> response surfaces `exploit_proposals` so the UI
    can render a section with the human's approved PoCs and the agent's
    skipped/declined records — without needing a separate endpoint."""
    fake_store = mocker.AsyncMock()
    fake_store.get_review = mocker.AsyncMock(
        return_value={
            "thread_id": "tid-2",
            "mode": "repo",
            "target_url": "https://github.com/o/r",
            "report_markdown": "## Security review\n\n…",
            "findings": [],
            "exploit_proposals": [
                {
                    "finding_id": "abc123",
                    "role": "injection",
                    "severity": "major",
                    "status": "approved",
                    "proposal_text": "SQL injection via login form",
                    "artifact": "curl -X POST …",
                    "confidence": 8,
                },
                {
                    "finding_id": "def456",
                    "role": "owasp",
                    "severity": "critical",
                    "status": "skipped_low_confidence",
                    "proposal_text": "",
                    "artifact": "",
                    "confidence": 3,
                },
            ],
        }
    )
    app.state.review_store = fake_store

    response = await http_client.get("/reviews/tid-2")

    assert response.status_code == 200
    body = response.json()
    assert "exploit_proposals" in body
    assert len(body["exploit_proposals"]) == 2
    statuses = {p["status"] for p in body["exploit_proposals"]}
    assert statuses == {"approved", "skipped_low_confidence"}
