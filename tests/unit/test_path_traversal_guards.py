"""Defence-in-depth against path traversal in two places where we read
files based on names that ORIGINALLY come from user-influenced input
(state checkpoint, HTTP path parameter).

Layer 1 — `_read_snapshot_file(snapshot_root, rel_path)` reads files
inside the cloned-repo snapshot. Today `rel_path` comes from
`list_repo_files` which is safe (it derives paths via `relative_to`),
but `RepoFile.path` survives in `ReviewState` and is reloaded from the
checkpointer. If a future code path accepts external state, a
`rel_path` of `../../etc/passwd` would resolve outside the snapshot
without defensive guards.

Layer 2 — `GET /reports/{filename}` serves the rendered review
markdown by name. Today the basic `/`/`\\` block prevents obvious
traversal, but a SYMLINK inside `reports/` (created by accident, by
a future bug, or by a malicious operator with shell access) would
make `Path.is_file()` traverse outside the directory anyway.

Both checks use `resolve()` + `is_relative_to(...)` so symlinks AND
`..` segments are caught.
"""

import pytest
from fastapi.testclient import TestClient

from main import create_test_app
from src.agents.base_reviewer import _read_snapshot_file

# ── Layer 1: snapshot read ─────────────────────────────────────────────


def test_read_snapshot_rejects_dotdot_escape(tmp_path):
    """`rel_path = ../secret` must not read outside `snapshot_root`."""
    snapshot_root = tmp_path / "snap"
    snapshot_root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("THIS IS OUTSIDE THE SNAPSHOT")

    result = _read_snapshot_file(snapshot_root, "../secret.txt")
    assert result == "", (
        "Reading a path that escapes the snapshot root via `..` must return "
        f"empty string (skip), got {result!r}"
    )


def test_read_snapshot_rejects_symlink_escape(tmp_path):
    """A symlink inside the snapshot pointing OUTSIDE it must not let
    the reader read outside. `resolve()` makes the check symlink-aware."""
    snapshot_root = tmp_path / "snap"
    snapshot_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("LEAKED")
    (snapshot_root / "bad").symlink_to(outside)

    result = _read_snapshot_file(snapshot_root, "bad")
    assert result == "", (
        "Symlink inside snapshot pointing outside must NOT be followed; "
        f"got {result!r}"
    )


def test_read_snapshot_allows_normal_file(tmp_path):
    """The guard must not break the happy path: a regular file inside
    the snapshot still reads."""
    snapshot_root = tmp_path / "snap"
    snapshot_root.mkdir()
    (snapshot_root / "app.py").write_text("print('hi')")

    assert _read_snapshot_file(snapshot_root, "app.py") == "print('hi')"


def test_read_snapshot_allows_nested_file(tmp_path):
    """Nested paths (the common case for repo trees) must still work."""
    snapshot_root = tmp_path / "snap"
    (snapshot_root / "src" / "lib").mkdir(parents=True)
    (snapshot_root / "src" / "lib" / "x.py").write_text("# x")

    assert _read_snapshot_file(snapshot_root, "src/lib/x.py") == "# x"


# ── Layer 2: /reports/{filename} symlink escape ────────────────────────


@pytest.fixture
def app(tmp_path, mocker):
    """Test app with a tmp reports_dir we can plant symlinks in."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    a = create_test_app(graph=mocker.AsyncMock(), github=mocker.AsyncMock())
    a.state.reports_dir = reports_dir
    return a, tmp_path, reports_dir


def test_reports_endpoint_serves_normal_file(app):
    a, _, reports_dir = app
    (reports_dir / "abc.md").write_text("# ok")
    client = TestClient(a)
    r = client.get("/reports/abc.md")
    assert r.status_code == 200
    assert "# ok" in r.text


def test_reports_endpoint_rejects_symlink_escape(app):
    """A symlink in reports/ pointing OUTSIDE reports_dir must NOT be
    followed. Without resolve()+is_relative_to() this would currently
    leak any file the app user can read."""
    a, tmp_path, reports_dir = app
    secret = tmp_path / "secret.txt"
    secret.write_text("LEAKED")
    (reports_dir / "leak.md").symlink_to(secret)

    client = TestClient(a)
    r = client.get("/reports/leak.md")
    assert r.status_code in (400, 403, 404), (
        f"symlinked file pointing outside reports_dir must be refused; "
        f"got {r.status_code} body={r.text!r}"
    )
    assert "LEAKED" not in r.text


def test_reports_endpoint_still_rejects_slash(app):
    """Existing guard against `/` and `\\` and leading `.` must still
    work — this PR doesn't widen the surface."""
    a, _, _ = app
    client = TestClient(a)
    assert client.get("/reports/sub/x.md").status_code in (400, 404)
    assert client.get("/reports/.env").status_code == 400
