"""WebSocket `/ws/chat/{thread_id}` must enforce an `Origin` allowlist.

Threat (review finding PR1.3): a malicious site loaded in the user's
browser can open a WebSocket to `ws://localhost:8000/ws/chat/<tid>` and
(a) leak the chat history, (b) send `approve <id>` to drive PoC
generation, (c) send `rerun:` prefixes to inject prompts. Browsers send
the `Origin` header on every WS handshake — we check it against
`settings.WS_ALLOWED_ORIGINS` and refuse anything that isn't there.

Non-browser clients (CLI smoke tests, curl-WS, `websockets` lib without
explicit headers) don't send `Origin` at all. We allow those through —
the threat surface here is browser CSRF, not arbitrary clients on the
internal network.

This is the internal-tool variant: no per-user tokens. The Origin check
is the entire defence.
"""

import pytest
from fastapi.testclient import TestClient

from main import create_test_app


@pytest.fixture
def app(mocker):
    return create_test_app(graph=mocker.AsyncMock(), github=mocker.AsyncMock())


def test_ws_accepts_missing_origin_header(app):
    """Non-browser clients (CLI tools, integration tests, the existing
    TestClient by default) don't send `Origin`. They must keep working —
    we're guarding against browser CSRF, not arbitrary in-cluster
    clients."""
    client = TestClient(app)
    with client.websocket_connect("/ws/chat/tid-noorigin") as ws:
        ws.send_json({"text": "hi"})
        msg = ws.receive_json()
        assert msg["text"] == "hi"


def test_ws_accepts_allowed_origin(app):
    """Browser opening the UI from `http://localhost:8000` is the normal
    path. The default allowlist must include it."""
    client = TestClient(app)
    with client.websocket_connect(
        "/ws/chat/tid-allow",
        headers={"origin": "http://localhost:8000"},
    ) as ws:
        ws.send_json({"text": "hi"})
        msg = ws.receive_json()
        assert msg["text"] == "hi"


def test_ws_rejects_disallowed_origin(app):
    """A browser tab on attacker.com that tries to open a WS to our
    instance gets refused. The starlette TestClient surfaces the close
    as a `WebSocketDisconnect` (subclasses depend on version, so we
    accept any exception during handshake)."""
    from starlette.websockets import WebSocketDisconnect

    client = TestClient(app)
    with pytest.raises((WebSocketDisconnect, Exception)):
        with client.websocket_connect(
            "/ws/chat/tid-block",
            headers={"origin": "https://attacker.example.com"},
        ) as ws:
            # If the close arrives during the handshake, the `with` body
            # may never run — but if it does, the next receive raises.
            ws.send_json({"text": "should never see this"})
            ws.receive_json()


def test_ws_honours_settings_allowlist(app, monkeypatch):
    """Operators with a custom UI host (corp domain, ngrok, etc.) add it
    via env: `WS_ALLOWED_ORIGINS=http://localhost:8000,https://reviews.corp.example`.
    """
    from src.utils import config as cfg

    monkeypatch.setattr(
        cfg.settings, "WS_ALLOWED_ORIGINS",
        ["http://localhost:8000", "https://reviews.corp.example"],
    )

    client = TestClient(app)
    with client.websocket_connect(
        "/ws/chat/tid-corp",
        headers={"origin": "https://reviews.corp.example"},
    ) as ws:
        ws.send_json({"text": "corp"})
        assert ws.receive_json()["text"] == "corp"


def test_ws_empty_allowlist_means_all_allowed(app, monkeypatch):
    """Escape hatch: setting `WS_ALLOWED_ORIGINS=` (empty list) disables
    the Origin check entirely. Useful for ops who don't want to enumerate
    every dev's localhost variant and accept the trade-off."""
    from src.utils import config as cfg

    monkeypatch.setattr(cfg.settings, "WS_ALLOWED_ORIGINS", [])

    client = TestClient(app)
    with client.websocket_connect(
        "/ws/chat/tid-any",
        headers={"origin": "https://literally-anything.com"},
    ) as ws:
        ws.send_json({"text": "ok"})
        assert ws.receive_json()["text"] == "ok"
