import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from main import create_test_app
from src.chat.store import ChatMessage, ChatStore


@pytest.fixture
def store():
    return ChatStore()


@pytest.fixture
def app(mocker, store):
    return create_test_app(
        graph=mocker.AsyncMock(),
        github=mocker.AsyncMock(),
        store=store,
    )


@pytest.fixture
async def http_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── HTTP endpoints ───────────────────────────────────────────────────────────


async def test_index_serves_html_with_form(http_client):
    response = await http_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<form" in response.text
    assert "pr_url" in response.text


async def test_chat_history_endpoint_returns_messages(http_client, store):
    store.append("tid-x", ChatMessage(role="user", text="hi"))
    store.append("tid-x", ChatMessage(role="agent", text="hello"))

    response = await http_client.get("/chat/tid-x/history")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["text"] == "hi"
    assert data[0]["role"] == "user"
    assert data[1]["text"] == "hello"


async def test_chat_history_unknown_thread_returns_empty(http_client):
    response = await http_client.get("/chat/nonexistent/history")
    assert response.status_code == 200
    assert response.json() == []


# ── WebSocket ────────────────────────────────────────────────────────────────


def test_websocket_broadcasts_user_message(app):
    client = TestClient(app)
    with client.websocket_connect("/ws/chat/tid-1") as ws:
        ws.send_json({"text": "hello"})
        data = ws.receive_json()
        assert data["role"] == "user"
        assert data["text"] == "hello"


def test_websocket_broadcasts_to_multiple_clients_same_thread(app):
    client = TestClient(app)
    with (
        client.websocket_connect("/ws/chat/tid-2") as ws1,
        client.websocket_connect("/ws/chat/tid-2") as ws2,
    ):
        ws1.send_json({"text": "hello"})

        m1 = ws1.receive_json()
        m2 = ws2.receive_json()
        assert m1["text"] == "hello"
        assert m2["text"] == "hello"


def test_websocket_isolates_threads(app):
    client = TestClient(app)
    with (
        client.websocket_connect("/ws/chat/tid-a") as ws_a,
        client.websocket_connect("/ws/chat/tid-b") as ws_b,
    ):
        ws_a.send_json({"text": "from a"})
        ws_b.send_json({"text": "from b"})

        m_a = ws_a.receive_json()
        m_b = ws_b.receive_json()
        # If isolation were broken, ws_b's first message would be ws_a's broadcast
        # because ws_a sent first and the broadcast would race-win.
        assert m_a["text"] == "from a"
        assert m_b["text"] == "from b"
