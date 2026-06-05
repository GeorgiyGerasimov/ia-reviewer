"""`ChatMessage` carries an optional `interrupt` payload so the UI can
render the exploit-approval buttons inline in the chat.

Shape of `interrupt`:
  {
    "kind": "exploit_approval",
    "finding_id": "a3f4b8c1d2e5",
    "role": "injection",
    "severity": "major",
  }

Plain user/agent text messages don't carry the field. `to_dict` must
omit the field entirely when it's None — the wire payload stays
backwards-compatible for tabs that don't know the new field.
"""

from src.chat.store import ChatMessage


def test_chat_message_to_dict_omits_interrupt_when_none():
    msg = ChatMessage(role="agent", text="hello")
    payload = msg.to_dict()
    assert "interrupt" not in payload
    assert payload["role"] == "agent"
    assert payload["text"] == "hello"


def test_chat_message_to_dict_serialises_interrupt_when_present():
    msg = ChatMessage(
        role="agent",
        text="Approve PoC for finding abc123?",
        interrupt={
            "kind": "exploit_approval",
            "finding_id": "abc123",
            "role": "injection",
            "severity": "major",
        },
    )
    payload = msg.to_dict()
    assert payload["interrupt"]["kind"] == "exploit_approval"
    assert payload["interrupt"]["finding_id"] == "abc123"
    assert payload["interrupt"]["role"] == "injection"
    assert payload["interrupt"]["severity"] == "major"
