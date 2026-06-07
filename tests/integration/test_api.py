import pytest
from httpx import ASGITransport, AsyncClient

from main import _run_review, create_test_app
from src.graph.state import ReviewRequest


@pytest.fixture
def app(mocker):
    return create_test_app(graph=mocker.AsyncMock(), github=mocker.AsyncMock())


@pytest.fixture
async def http_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── /health ──────────────────────────────────────────────────────────────────


async def test_health_returns_ok(http_client):
    response = await http_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # `langfuse_url` is always present in the response body. It's `None`
    # when tracing is off (the UI keeps the "View traces" link hidden),
    # or a string URL when a CallbackHandler is wired. Tests use
    # `create_test_app` without `langfuse_callback` → expect `None` here.
    assert body["langfuse_url"] is None


async def test_health_advertises_langfuse_url_when_tracing_is_wired(mocker):
    """When a CallbackHandler is attached, /health includes the
    LANGFUSE_HOST so the UI can link to it.
    """
    from httpx import ASGITransport, AsyncClient

    from main import create_test_app

    handler = mocker.MagicMock(name="CallbackHandler")
    app = create_test_app(
        graph=mocker.AsyncMock(),
        github=mocker.AsyncMock(),
        langfuse_callback=handler,
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # Default-compose / .env.example value
    assert body["langfuse_url"].startswith("http")


# ── POST /review validation ──────────────────────────────────────────────────


async def test_review_rejects_missing_pr_url(http_client):
    response = await http_client.post("/review", json={})
    assert response.status_code == 400
    assert "pr_url" in response.json()["error"]


async def test_review_rejects_invalid_scope(http_client):
    response = await http_client.post(
        "/review",
        json={"pr_url": "https://github.com/o/r/pull/1", "scope": ["mobile"]},
    )
    assert response.status_code == 400
    assert "scope" in response.json()["error"]


async def test_review_returns_thread_id_on_valid_request(http_client):
    response = await http_client.post(
        "/review",
        json={"pr_url": "https://github.com/o/r/pull/1"},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "started"
    assert "thread_id" in body
    assert len(body["thread_id"]) > 0


# ── _run_review behaviour ────────────────────────────────────────────────────


async def test_run_review_passes_scope_and_thread_id(mocker):
    fake_app = mocker.MagicMock()
    # Disable tracing for this test — its contract is "scope + thread_id flow
    # through", not "callbacks are attached" (covered in test_tracing_wiring).
    fake_app.state.langfuse_callback = None
    fake_app.state.github.fetch_pr = mocker.AsyncMock(
        return_value=ReviewRequest(
            pr_url="https://github.com/o/r/pull/1",
            diff="d",
            files_changed=["a.py"],
            author="u",
        )
    )
    # _run_review uses graph.astream (mode=updates) so the workflow-diagram
    # in the UI can light up each node circle as it completes. Capture the
    # state/config pair via a real async generator instead of an AsyncMock.
    invocations: list[tuple] = []

    async def fake_astream(state, config=None):
        invocations.append((state, config))
        yield {"publish_report": {}}

    fake_app.state.graph.astream = fake_astream

    await _run_review(fake_app, "https://github.com/o/r/pull/1", ["injection"], "tid-x")

    fake_app.state.github.fetch_pr.assert_awaited_once_with("https://github.com/o/r/pull/1")
    assert len(invocations) == 1
    state, config = invocations[0]
    assert state.request.scope == ["injection"]
    assert state.thread_id == "tid-x"
    # `callbacks` always carries the per-request TokenUsageHandler
    # (langfuse_callback is disabled in this test fixture).
    assert config["configurable"] == {"thread_id": "tid-x"}
    assert "callbacks" in config and len(config["callbacks"]) == 1
