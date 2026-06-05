"""WebSocket replays stored progress events on connect.

Without this, the workflow-diagram in the UI can miss the early progress
events that fired before the client opened the WebSocket (the review's
BackgroundTask starts immediately after the 202 response). Replay on
connect ensures the diagram reaches a consistent state even if the user
opens the tab late.
"""

from fastapi.testclient import TestClient

from main import create_test_app
from src.chat.progress_store import ProgressStore


def test_ws_replays_progress_on_connect(mocker):
    """Connecting to a thread that already has stored progress events
    receives them all in order, before any new live events.
    """
    progress_store = ProgressStore()
    progress_store.append("tid-x", {"type": "progress", "node": "validate_request"})
    progress_store.append("tid-x", {"type": "progress", "node": "dependency_review"})

    app = create_test_app(graph=mocker.AsyncMock(), github=mocker.AsyncMock())
    app.state.progress_store = progress_store

    client = TestClient(app)
    with client.websocket_connect("/ws/chat/tid-x") as ws:
        first = ws.receive_json()
        second = ws.receive_json()
        assert first == {"type": "progress", "node": "validate_request"}
        assert second == {"type": "progress", "node": "dependency_review"}
