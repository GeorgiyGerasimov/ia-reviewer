"""`ExploitProposal.save_mode` — where the artifact lives once generated.

`embed`  → rendered inline in the main `reports/<thread_id>.md` (the
           default, matches the original Phase C behaviour).
`file`   → written to its own `reports/<thread_id>.exploit.<finding_id>.md`
           and only referenced (by relative URL) from the main report.

The chat UI exposes both as separate buttons; the agent's `_parse_resume`
maps the trailing token (`embed` / `file` / absent) to this field. Default
is `embed` so old persisted rows + tests that don't set it keep working.
"""

from src.graph.state import ExploitProposal


def test_exploit_proposal_save_mode_defaults_to_embed():
    ep = ExploitProposal(
        finding_id="abc123",
        role="injection",
        severity="major",
        proposal_text="poc",
        status="approved",
    )
    assert ep.save_mode == "embed"


def test_exploit_proposal_save_mode_accepts_file():
    ep = ExploitProposal(
        finding_id="abc123",
        role="injection",
        severity="major",
        proposal_text="poc",
        status="approved",
        save_mode="file",
    )
    assert ep.save_mode == "file"
