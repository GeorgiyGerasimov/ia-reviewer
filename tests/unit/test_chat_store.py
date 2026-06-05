from src.chat.store import ChatMessage, ChatStore


def test_chat_store_appends_messages():
    store = ChatStore()
    store.append("tid-1", ChatMessage(role="user", text="hello"))
    store.append("tid-1", ChatMessage(role="agent", text="hi"))

    history = store.get_history("tid-1")
    assert len(history) == 2
    assert history[0].text == "hello"
    assert history[0].role == "user"
    assert history[1].text == "hi"
    assert history[1].role == "agent"


def test_chat_store_isolates_threads():
    store = ChatStore()
    store.append("tid-a", ChatMessage(role="user", text="a"))
    store.append("tid-b", ChatMessage(role="user", text="b"))

    history_a = store.get_history("tid-a")
    history_b = store.get_history("tid-b")

    assert [m.text for m in history_a] == ["a"]
    assert [m.text for m in history_b] == ["b"]


def test_chat_store_returns_empty_for_unknown_thread():
    store = ChatStore()
    assert store.get_history("nonexistent") == []
