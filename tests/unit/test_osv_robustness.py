"""OSV client robustness:

  PR2.5.a — one malformed advisory id must NOT break the whole scan.
  Today `_fetch_detail` calls `response.raise_for_status()`; a 404 / 500
  on a single id propagates out of `query()` so the surrounding
  `DependencyScanner.scan` catches it and returns `findings=[]` for
  every package — total signal loss.

  PR2.5.b — the client can be constructed with an injected
  `httpx.AsyncClient`, so production can share a single client across
  the process lifetime (lifespan-managed) instead of building one per
  `query()` call. The default behaviour (no client passed) still
  works, just less efficiently.
"""

import httpx
import pytest
import respx

from src.integrations.osv_client import Dep, OSVClient


@pytest.fixture
def client():
    return OSVClient()


@respx.mock
async def test_query_skips_single_404_advisory_id(client):
    """One advisory returning 404 must not lose the OTHER findings. We
    skip the bad id and surface every other vuln we successfully fetched.
    """
    deps = [
        Dep(name="lodash", ecosystem="npm", version="4.17.15"),
        Dep(name="express", ecosystem="npm", version="4.18.2"),
    ]
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        return_value=httpx.Response(200, json={"results": [
            {"vulns": [{"id": "GHSA-lodash", "modified": "2024-01-01T00:00:00Z"}]},
            {"vulns": [{"id": "GHSA-broken", "modified": "2024-01-01T00:00:00Z"}]},
        ]})
    )
    respx.get("https://api.osv.dev/v1/vulns/GHSA-lodash").mock(
        return_value=httpx.Response(200, json={
            "id": "GHSA-lodash", "aliases": [], "summary": "real vuln",
            "affected": [], "references": [],
        })
    )
    respx.get("https://api.osv.dev/v1/vulns/GHSA-broken").mock(
        return_value=httpx.Response(404, text="not found")
    )

    result = await client.query(deps)

    # `lodash` keeps its vuln — the broken `express` advisory is silently
    # dropped (we don't fabricate a Vuln we couldn't fetch).
    assert len(result[deps[0]]) == 1
    assert result[deps[0]][0].id == "GHSA-lodash"
    assert result[deps[1]] == [], (
        f"the broken advisory id must drop to empty for that dep instead "
        f"of bubbling up; got {result[deps[1]]!r}"
    )


@respx.mock
async def test_query_skips_500_advisory(client):
    """Transient 5xx on a single detail call must not abort the whole
    scan either."""
    dep = Dep(name="x", ecosystem="npm", version="1.0.0")
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        return_value=httpx.Response(200, json={"results": [
            {"vulns": [{"id": "GHSA-fivexx", "modified": "2024-01-01T00:00:00Z"}]},
        ]})
    )
    respx.get("https://api.osv.dev/v1/vulns/GHSA-fivexx").mock(
        return_value=httpx.Response(503, text="upstream down")
    )

    result = await client.query([dep])
    assert result[dep] == []


@respx.mock
async def test_query_skips_network_error_on_detail(client):
    """`httpx.ConnectError` / `ReadTimeout` for one id behaves the same
    way: surface what we got, log + drop the rest."""
    dep = Dep(name="y", ecosystem="npm", version="1.0.0")
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        return_value=httpx.Response(200, json={"results": [
            {"vulns": [{"id": "GHSA-neterr", "modified": "2024-01-01T00:00:00Z"}]},
        ]})
    )
    respx.get("https://api.osv.dev/v1/vulns/GHSA-neterr").mock(
        side_effect=httpx.ConnectError("net down")
    )

    result = await client.query([dep])
    assert result[dep] == []


@respx.mock
async def test_client_accepts_injected_httpx_client():
    """An injected `httpx.AsyncClient` lets the lifespan share a
    connection pool across all reviews. Default (no client) still
    works — the client opens and closes its own."""
    deps = [Dep(name="z", ecosystem="npm", version="1.0.0")]
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        return_value=httpx.Response(200, json={"results": [{}]})
    )

    async with httpx.AsyncClient() as shared:
        osv = OSVClient(http_client=shared)
        result = await osv.query(deps)
        assert result[deps[0]] == []
        # The shared client must NOT be closed by `query` — it has to
        # survive for the next call.
        assert not shared.is_closed
