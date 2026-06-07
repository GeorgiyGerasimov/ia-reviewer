"""GET /reviews/{thread_id}/critical-findings.

The UI's "Critical findings" panel calls this endpoint to render a row
per critical finding with its current exploit status (none / approved /
declined / skipped_*). Without this endpoint the UI would have to
join `findings` and `exploit_proposals` from the generic /reviews/{tid}
response on its own, which is fragile and bloats the payload.

Contract:
  * Returns 200 with a JSON list ordered by (role, file, line) so the
    UI gets a stable display order.
  * Each row carries: finding_id (stable 12-char content hash),
    role, severity, file, line, issue, exploit_status (null when no
    exploit was attempted), confidence (only present for attempts).
  * 404 when the thread is unknown OR review_store is not wired.
  * Returns [] (not 404) when the thread is known but has zero
    critical findings — the UI renders an empty panel.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from main import create_test_app
from src.agents.exploit_proposal import compute_finding_id


@pytest.fixture
def app(mocker):
    return create_test_app(graph=mocker.AsyncMock(), github=mocker.AsyncMock())


@pytest.fixture
async def http_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_returns_404_when_review_store_disabled(http_client, app):
    """No DATABASE_URL → no store wired → 404. Same shape as
    /reviews/{tid}: the UI treats a missing store as a missing review."""
    app.state.review_store = None

    response = await http_client.get("/reviews/any-tid/critical-findings")

    assert response.status_code == 404


async def test_returns_404_when_review_missing(http_client, app, mocker):
    fake_store = mocker.AsyncMock()
    fake_store.get_review = mocker.AsyncMock(return_value=None)
    app.state.review_store = fake_store

    response = await http_client.get("/reviews/nonexistent/critical-findings")

    assert response.status_code == 404


async def test_returns_empty_list_when_no_critical_findings(http_client, app, mocker):
    """Review exists but every finding is `major`/`minor`/`info`."""
    fake_store = mocker.AsyncMock()
    fake_store.get_review = mocker.AsyncMock(
        return_value={
            "thread_id": "tid-1",
            "findings": [
                {"role": "injection", "file": "a.py", "line": 1, "severity": "major", "issue": "x"},
                {"role": "owasp", "file": "b.py", "line": 2, "severity": "minor", "issue": "y"},
            ],
            "exploit_proposals": [],
        }
    )
    app.state.review_store = fake_store

    response = await http_client.get("/reviews/tid-1/critical-findings")

    assert response.status_code == 200
    assert response.json() == []


async def test_returns_critical_findings_with_finding_id(http_client, app, mocker):
    """The finding_id is the SAME stable content hash used everywhere
    else (compute_finding_id) so a row's id matches the POST endpoint."""
    crit_finding = {
        "role": "injection",
        "file": "src/auth.py",
        "line": 42,
        "severity": "critical",
        "issue": "SQL injection via login",
    }
    fake_store = mocker.AsyncMock()
    fake_store.get_review = mocker.AsyncMock(
        return_value={
            "thread_id": "tid-1",
            "findings": [crit_finding],
            "exploit_proposals": [],
        }
    )
    app.state.review_store = fake_store

    response = await http_client.get("/reviews/tid-1/critical-findings")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    expected_id = compute_finding_id("injection", crit_finding)
    assert rows[0]["finding_id"] == expected_id
    assert rows[0]["role"] == "injection"
    assert rows[0]["file"] == "src/auth.py"
    assert rows[0]["line"] == 42
    assert rows[0]["issue"] == "SQL injection via login"
    # No exploit attempted yet → status is null.
    assert rows[0]["exploit_status"] is None


async def test_critical_findings_carry_exploit_status_when_attempted(
    http_client, app, mocker
):
    """When an exploit was already generated, the row carries the full
    payload (status, confidence, proposal_text, artifact) so the UI can
    render the inline <details> block from a single fetch — no second
    round-trip to /reviews/{tid} to pick up the artifact body."""
    crit_finding = {
        "role": "injection",
        "file": "src/auth.py",
        "line": 42,
        "severity": "critical",
        "issue": "SQL injection",
    }
    fid = compute_finding_id("injection", crit_finding)

    fake_store = mocker.AsyncMock()
    fake_store.get_review = mocker.AsyncMock(
        return_value={
            "thread_id": "tid-1",
            "findings": [crit_finding],
            "exploit_proposals": [
                {
                    "finding_id": fid,
                    "role": "injection",
                    "severity": "critical",
                    "status": "approved",
                    "proposal_text": "Attacker crafts UNION SELECT …",
                    "artifact": "curl -X POST http://localhost:8000/login …",
                    "confidence": 8,
                }
            ],
        }
    )
    app.state.review_store = fake_store

    response = await http_client.get("/reviews/tid-1/critical-findings")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["finding_id"] == fid
    assert rows[0]["exploit_status"] == "approved"
    assert rows[0]["confidence"] == 8
    # Inline-display fields — UI renders them in a <details> block under
    # the row, no separate fetch.
    assert rows[0]["proposal_text"].startswith("Attacker crafts")
    assert rows[0]["artifact"].startswith("curl -X POST")


async def test_critical_findings_survives_corrupted_jsonb_rows(
    http_client, app, mocker
):
    """Defensive: a buggy older write path stored some exploit_proposals
    entries as double-JSON-encoded strings. The endpoint must not 500
    on those — it should decode what it can and serve the row.

    Reproduces the 2026-06-07 incident where /critical-findings 500'd
    with `AttributeError: 'str' object has no attribute 'get'`.
    """
    import json

    crit_finding = {
        "role": "injection",
        "file": "src/auth.py",
        "line": 42,
        "severity": "critical",
        "issue": "SQL injection",
    }
    fid = compute_finding_id("injection", crit_finding)

    # Real-world corrupted shape: the production row had a nested
    # string-of-array-of-dict — i.e. the entry is a JSON string whose
    # value is itself a JSON array `[{...}]`. The decoder must unwrap
    # both layers.
    nested_payload = json.dumps([
        {
            "finding_id": fid,
            "role": "injection",
            "severity": "critical",
            "status": "approved",
            "proposal_text": "stale",
            "artifact": "x",
            "confidence": 7,
        }
    ])

    fake_store = mocker.AsyncMock()
    fake_store.get_review = mocker.AsyncMock(
        return_value={
            "thread_id": "tid-corrupt",
            "findings": [crit_finding],
            "exploit_proposals": [nested_payload],
        }
    )
    app.state.review_store = fake_store

    response = await http_client.get("/reviews/tid-corrupt/critical-findings")

    assert response.status_code == 200
    rows = response.json()
    # The corrupted entry was decoded → status surfaces correctly.
    assert len(rows) == 1
    assert rows[0]["exploit_status"] == "approved"
    assert rows[0]["confidence"] == 7


async def test_critical_findings_proposal_fields_absent_when_not_attempted(
    http_client, app, mocker
):
    """For rows where no exploit was generated yet, the proposal_text /
    artifact fields are explicitly null — the UI distinguishes 'never
    attempted' from 'attempted but empty'."""
    crit_finding = {
        "role": "owasp",
        "file": "src/x.py",
        "line": 1,
        "severity": "critical",
        "issue": "broken access control",
    }
    fake_store = mocker.AsyncMock()
    fake_store.get_review = mocker.AsyncMock(
        return_value={
            "thread_id": "tid-1",
            "findings": [crit_finding],
            "exploit_proposals": [],
        }
    )
    app.state.review_store = fake_store

    response = await http_client.get("/reviews/tid-1/critical-findings")

    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["exploit_status"] is None
    assert rows[0]["proposal_text"] is None
    assert rows[0]["artifact"] is None


async def test_returns_only_critical_severity(http_client, app, mocker):
    """Major/minor/info findings are not included. Critical-only is the
    same policy as the exploit branch — see _QUALIFYING_SEVERITIES."""
    fake_store = mocker.AsyncMock()
    fake_store.get_review = mocker.AsyncMock(
        return_value={
            "thread_id": "tid-1",
            "findings": [
                {"role": "injection", "file": "a.py", "line": 1, "severity": "critical", "issue": "real"},
                {"role": "owasp", "file": "b.py", "line": 2, "severity": "major", "issue": "skip"},
                {"role": "dependency", "file": "c.txt", "line": 3, "severity": "minor", "issue": "skip"},
                {"role": "configuration", "file": "d.yml", "line": 4, "severity": "info", "issue": "skip"},
            ],
            "exploit_proposals": [],
        }
    )
    app.state.review_store = fake_store

    response = await http_client.get("/reviews/tid-1/critical-findings")

    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["severity"] == "critical"
    assert rows[0]["issue"] == "real"
