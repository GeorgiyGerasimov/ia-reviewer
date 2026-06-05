"""Resume-payload parsing for exploit-approval interrupts.

Chat now exposes three buttons per pending finding:

  • Approve · embed in report     → sends 'approve <id> embed'
  • Approve · save as file         → sends 'approve <id> file'
  • Decline                        → sends 'decline <id>'

Plus the legacy text form `approve <id>` (no suffix) which defaults to
`embed` for backward-compat with anyone typing it.

`_parse_decision(answer, finding_id)` returns `(approved: bool, save_mode: str)`:
"""

from src.agents.exploit_proposal import _parse_decision

FID = "a3f4b8c1d2e5"


def test_parse_decision_approve_embed_explicit():
    assert _parse_decision(f"approve {FID} embed", FID) == (True, "embed")


def test_parse_decision_approve_file_explicit():
    assert _parse_decision(f"approve {FID} file", FID) == (True, "file")


def test_parse_decision_approve_without_suffix_defaults_to_embed():
    """Legacy text form `approve <id>` is treated as embed (default)."""
    assert _parse_decision(f"approve {FID}", FID) == (True, "embed")


def test_parse_decision_approve_alone_matches_when_caller_passes_correct_id():
    """`approve` with no id at all → caller (loop) is expected to ensure
    there's only one pending finding. Default mode `embed`."""
    assert _parse_decision("approve", FID) == (True, "embed")


def test_parse_decision_decline_returns_false():
    assert _parse_decision(f"decline {FID}", FID) == (False, "embed")


def test_parse_decision_decline_without_id():
    assert _parse_decision("decline", FID) == (False, "embed")


def test_parse_decision_unknown_text_is_treated_as_decline():
    """Random text → not approved, save_mode irrelevant (will be 'embed')."""
    assert _parse_decision("yes please", FID) == (False, "embed")


def test_parse_decision_approve_wrong_id_does_not_match():
    """`approve <other-id>` for a finding with `<id>` → not approved."""
    assert _parse_decision("approve different123 embed", FID) == (False, "embed")


def test_parse_decision_is_case_insensitive():
    assert _parse_decision(f"APPROVE {FID.upper()} FILE", FID) == (True, "file")


def test_parse_decision_unknown_suffix_falls_back_to_embed():
    """If someone types `approve <id> potato` we still approve, defaulting
    to embed rather than rejecting the whole reply."""
    assert _parse_decision(f"approve {FID} potato", FID) == (True, "embed")
