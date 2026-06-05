"""Integration tests for POST /review accepting a repo-shaped payload.

Two valid payload shapes:
  {"pr_url": "https://github.com/o/r/pull/42", ...}   → PR-mode review
  {"repo_url": "https://github.com/o/r", "ref": ...}  → repo-mode review

Mixed (both keys) or empty (neither key) payloads are rejected as 400.
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


async def test_review_endpoint_accepts_repo_url(http_client):
    response = await http_client.post(
        "/review",
        json={"repo_url": "https://github.com/o/r", "ref": "main"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "started"
    assert "thread_id" in body and len(body["thread_id"]) > 0
    assert body.get("repo_url") == "https://github.com/o/r"


async def test_review_endpoint_rejects_when_neither_url(http_client):
    response = await http_client.post("/review", json={})
    assert response.status_code == 400
    err = response.json()["error"]
    # Error text mentions both fields so the caller knows which to provide.
    assert "pr_url" in err and "repo_url" in err


async def test_review_endpoint_rejects_when_both_urls(http_client):
    response = await http_client.post(
        "/review",
        json={
            "pr_url": "https://github.com/o/r/pull/1",
            "repo_url": "https://github.com/o/r",
        },
    )
    assert response.status_code == 400
    err = response.json()["error"]
    assert "mixed" in err.lower() or ("pr_url" in err and "repo_url" in err)


async def test_get_report_endpoint_serves_markdown(http_client, app, tmp_path):
    """`GET /reports/{thread_id}.md` returns the saved repo-mode report verbatim
    with a markdown content-type. The endpoint reads from the directory
    configured on app.state.reports_dir, isolated to tmp_path in tests."""
    app.state.reports_dir = tmp_path
    body = "## Security review\n\nfresh repo report\n"
    (tmp_path / "t-42.md").write_text(body)

    response = await http_client.get("/reports/t-42.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.text == body


async def test_get_report_endpoint_returns_404_for_unknown_id(http_client, app, tmp_path):
    app.state.reports_dir = tmp_path
    response = await http_client.get("/reports/nonexistent.md")
    assert response.status_code == 404


async def test_review_endpoint_rejects_invalid_repo_url(http_client):
    """Malformed repo_url is caught at the endpoint with a 400 + descriptive
    error, not deferred until the background task crashes on `git clone`."""
    response = await http_client.post(
        "/review",
        json={"repo_url": "not-a-url"},
    )
    assert response.status_code == 400
    assert "repo_url" in response.json()["error"]


async def test_review_endpoint_normalizes_tree_url_and_uses_extracted_ref(http_client, app, mocker):
    """A `/tree/<ref>` browser URL is normalised to the canonical
    `https://github.com/<owner>/<repo>` and the extracted ref is forwarded
    to `_run_repo_review` (unless the user supplied their own)."""
    captured: dict = {}

    async def fake_run_repo_review(application, repo_url, ref, scope, thread_id):
        captured["repo_url"] = repo_url
        captured["ref"] = ref

    mocker.patch("main._run_repo_review", side_effect=fake_run_repo_review)

    response = await http_client.post(
        "/review",
        json={"repo_url": "https://github.com/octo/myrepo/tree/develop"},
    )
    assert response.status_code == 202
    # Give the BackgroundTask a beat to fire.
    import asyncio

    await asyncio.sleep(0.05)
    assert captured["repo_url"] == "https://github.com/octo/myrepo"
    assert captured["ref"] == "develop"
