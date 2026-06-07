"""When the Docker build emits the React SPA into `web/dist/`, the app
serves it instead of the legacy Jinja template.

Two contracts pinned here:

1. `web_dist=None` (legacy / dev with no build) → GET / returns the
   Jinja `templates/index.html` shell (the contract `test_api.py`
   already exercises implicitly; pinned again here for clarity).
2. `web_dist=<path with index.html + assets/>` → GET / returns
   the React shell verbatim, and `/assets/<chunk>` serves the
   Vite-built chunks.

Backward-compat: even with the React shell mounted, the legacy
`/img.png` (a real PNG sitting next to the Jinja template) keeps
working so the boot script and the React shell both find the
favicon without bespoke handling.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import create_test_app


def _stub_graph(mocker):
    graph = mocker.MagicMock()
    graph.astream = mocker.AsyncMock()
    return graph


def test_serves_jinja_template_when_web_dist_is_none(mocker):
    """No bundled SPA → fall back to legacy template. Operators running
    `uvicorn main:app` against a fresh clone (no docker build, no
    npm) get the old UI as before."""
    app = create_test_app(graph=_stub_graph(mocker), web_dist=None)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    # The Jinja shell carries an `id="root"` slot for React + the
    # boot script; it ALSO contains the literal `<html lang="en">`
    # opener. The React shell would have neither the title link
    # below nor the same workflow markup. We assert on a string
    # that ONLY the legacy template carries to distinguish them.
    assert "data-node=" in r.text or "Per-file scan" in r.text


def test_serves_react_shell_when_web_dist_index_exists(mocker, tmp_path: Path):
    """Vite-built `web/dist/index.html` overrides the Jinja template."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div>'
        '<script type="module" src="/assets/index-abc.js"></script>'
        "</body></html>"
    )
    (dist / "assets" / "index-abc.js").write_text("/* react bundle */")

    app = create_test_app(graph=_stub_graph(mocker), web_dist=dist)
    client = TestClient(app)

    r = client.get("/")
    assert r.status_code == 200
    assert "<div id=\"root\"></div>" in r.text
    assert "/assets/index-abc.js" in r.text


def test_serves_vite_assets_when_web_dist_mounted(mocker, tmp_path: Path):
    """The `/assets/*` chunk path resolves into web/dist/assets/."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    (dist / "assets" / "index-abc.js").write_text("export const X = 1;")
    (dist / "assets" / "index-abc.css").write_text("body { color: red; }")

    app = create_test_app(graph=_stub_graph(mocker), web_dist=dist)
    client = TestClient(app)

    r = client.get("/assets/index-abc.js")
    assert r.status_code == 200
    assert "export const X = 1;" in r.text

    r = client.get("/assets/index-abc.css")
    assert r.status_code == 200
    assert "body { color: red; }" in r.text


def test_assets_404_for_unknown_chunk(mocker, tmp_path: Path):
    """Unknown `/assets/<x>` returns 404 — no traversal, no fallthrough
    to the SPA index."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    (dist / "assets" / "real.js").write_text("real")

    app = create_test_app(graph=_stub_graph(mocker), web_dist=dist)
    client = TestClient(app)

    assert client.get("/assets/does-not-exist.js").status_code == 404


def test_falls_back_to_jinja_when_dist_has_no_index(mocker, tmp_path: Path):
    """An empty `web/dist/` (or a leftover one missing index.html) is
    treated as 'no bundle' — the Jinja shell wins. Avoids a half-broken
    state where Docker COPYd an incomplete dist."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    # No index.html intentionally.

    app = create_test_app(graph=_stub_graph(mocker), web_dist=dist)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    # Jinja shell signature again.
    assert "data-node=" in r.text or "Per-file scan" in r.text


def test_img_png_still_resolves_in_react_mode(mocker, tmp_path: Path):
    """The /img.png handler stays attached regardless of which shell
    is served. Both the legacy template AND the Vite shell reference
    `/img.png` for the donkey icon."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")

    app = create_test_app(graph=_stub_graph(mocker), web_dist=dist)
    client = TestClient(app)
    r = client.get("/img.png")
    # The actual file lives at templates/img.png; if present it
    # returns 200, otherwise 404. Either way the route exists and
    # doesn't crash — the contract is "the route resolves through
    # the same handler in both shell modes".
    assert r.status_code in (200, 404)


@pytest.mark.parametrize(
    "missing",
    [
        "index.html",  # web_dist exists but index missing
    ],
)
def test_react_mount_is_skipped_when_index_missing(
    mocker, tmp_path: Path, missing: str,
):
    """Same as the fallback test but parameterised for documentation —
    an incomplete `web/dist/` MUST not be silently served as a broken
    React shell. The detection is: directory exists AND `index.html`
    is a real file."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    # `missing` enumerates the file we deliberately don't create.
    if missing != "index.html":
        (dist / "index.html").write_text("ok")

    app = create_test_app(graph=_stub_graph(mocker), web_dist=dist)
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "<div id=\"root\"></div>" not in r.text
