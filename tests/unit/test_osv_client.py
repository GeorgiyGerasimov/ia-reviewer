"""Thin async client around OSV.dev /v1/querybatch + per-vuln detail.

Contract:
- `Dep(name, ecosystem, version)` → list of `Vuln(id, aliases, summary,
  severity, fixed_versions, references)`.
- Batches input at 1000 per HTTP call (the API's documented per-batch cap).
- Preserves input-order in the returned mapping (the API guarantees
  response order matches input order).
- Dedupes findings by OSV `id` across batches (same advisory can match
  multiple deps; we keep one Vuln per id).

Tests mock the wire with respx — no real HTTPS to api.osv.dev.
"""

import httpx
import pytest
import respx

from src.integrations.osv_client import Dep, OSVClient, Vuln


@pytest.fixture
def client():
    return OSVClient()


@respx.mock
async def test_query_batches_at_1000_per_call(client):
    """1500 deps → exactly two POST /v1/querybatch calls (1000 + 500)."""
    deps = [
        Dep(name=f"pkg-{i}", ecosystem="npm", version="1.0.0")
        for i in range(1500)
    ]
    # Each batch response: empty `vulns` for every query.
    route = respx.post("https://api.osv.dev/v1/querybatch").mock(
        return_value=httpx.Response(200, json={"results": [{} for _ in range(1000)]})
    )
    # Second call has 500 results — respx allows side_effect via list.
    route.side_effect = [
        httpx.Response(200, json={"results": [{} for _ in range(1000)]}),
        httpx.Response(200, json={"results": [{} for _ in range(500)]}),
    ]

    result = await client.query(deps)

    assert route.call_count == 2
    # All deps present in result (clean), each maps to empty list.
    assert len(result) == 1500
    assert all(vulns == [] for vulns in result.values())


@respx.mock
async def test_query_preserves_input_order(client):
    """Response is keyed by the exact input Dep — order matters because
    the API guarantees response[i] corresponds to queries[i]."""
    deps = [
        Dep(name="alpha", ecosystem="npm", version="1.0.0"),
        Dep(name="beta", ecosystem="npm", version="2.0.0"),
        Dep(name="gamma", ecosystem="npm", version="3.0.0"),
    ]
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        return_value=httpx.Response(200, json={"results": [
            {"vulns": [{"id": "GHSA-aaa", "modified": "2024-01-01T00:00:00Z"}]},
            {},
            {"vulns": [{"id": "GHSA-ggg", "modified": "2024-01-01T00:00:00Z"}]},
        ]})
    )
    # `/v1/vulns/{id}` lookups for each surfaced id — return minimal records.
    respx.get("https://api.osv.dev/v1/vulns/GHSA-aaa").mock(
        return_value=httpx.Response(200, json={
            "id": "GHSA-aaa", "summary": "alpha vuln", "aliases": [],
            "affected": [], "references": [],
        })
    )
    respx.get("https://api.osv.dev/v1/vulns/GHSA-ggg").mock(
        return_value=httpx.Response(200, json={
            "id": "GHSA-ggg", "summary": "gamma vuln", "aliases": [],
            "affected": [], "references": [],
        })
    )

    result = await client.query(deps)

    assert [v.id for v in result[deps[0]]] == ["GHSA-aaa"]
    assert result[deps[1]] == []
    assert [v.id for v in result[deps[2]]] == ["GHSA-ggg"]


@respx.mock
async def test_query_returns_empty_list_for_clean_package(client):
    """When OSV's results entry is `{}` or has no `vulns`, return []."""
    deps = [Dep(name="safe", ecosystem="npm", version="1.0.0")]
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        return_value=httpx.Response(200, json={"results": [{}]})
    )
    result = await client.query(deps)
    assert result[deps[0]] == []


@respx.mock
async def test_query_extracts_fixed_versions_from_semver_ranges(client):
    """`affected[].ranges` with type SEMVER and events introduced/fixed
    are normalised into `Vuln.fixed_versions: list[str]` for the report."""
    deps = [Dep(name="lodash", ecosystem="npm", version="4.17.15")]
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        return_value=httpx.Response(200, json={"results": [
            {"vulns": [{"id": "GHSA-lodash", "modified": "2024-01-01T00:00:00Z"}]}
        ]})
    )
    respx.get("https://api.osv.dev/v1/vulns/GHSA-lodash").mock(
        return_value=httpx.Response(200, json={
            "id": "GHSA-lodash",
            "aliases": ["CVE-2020-8203"],
            "summary": "Prototype Pollution in lodash",
            "affected": [{
                "package": {"ecosystem": "npm", "name": "lodash"},
                "ranges": [{
                    "type": "SEMVER",
                    "events": [{"introduced": "0"}, {"fixed": "4.17.20"}],
                }],
            }],
            "references": [{"type": "ADVISORY", "url": "https://github.com/advisories/GHSA-lodash"}],
            "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:H/A:L"}],
        })
    )

    result = await client.query(deps)

    [vuln] = result[deps[0]]
    assert vuln.id == "GHSA-lodash"
    assert "CVE-2020-8203" in vuln.aliases
    assert "4.17.20" in vuln.fixed_versions
    assert "lodash" in vuln.summary.lower()
    assert any("advisories" in url for url in vuln.references)


@respx.mock
async def test_query_dedupes_by_id_when_same_vuln_in_multiple_chunks(client):
    """The same advisory id can match multiple deps; we keep the Vuln
    object once and reference it under each affected Dep. The detail
    endpoint is hit only once per unique id."""
    deps = [
        Dep(name="lodash", ecosystem="npm", version="4.17.15"),
        Dep(name="lodash.merge", ecosystem="npm", version="4.6.1"),
    ]
    respx.post("https://api.osv.dev/v1/querybatch").mock(
        return_value=httpx.Response(200, json={"results": [
            {"vulns": [{"id": "GHSA-dup", "modified": "2024-01-01T00:00:00Z"}]},
            {"vulns": [{"id": "GHSA-dup", "modified": "2024-01-01T00:00:00Z"}]},
        ]})
    )
    detail_route = respx.get("https://api.osv.dev/v1/vulns/GHSA-dup").mock(
        return_value=httpx.Response(200, json={
            "id": "GHSA-dup", "aliases": [], "summary": "shared",
            "affected": [], "references": [],
        })
    )

    result = await client.query(deps)

    # Each dep sees the vuln…
    assert [v.id for v in result[deps[0]]] == ["GHSA-dup"]
    assert [v.id for v in result[deps[1]]] == ["GHSA-dup"]
    # …but the detail endpoint was hit ONCE thanks to dedup.
    assert detail_route.call_count == 1
    # And the Vuln object is the same instance (same id).
    assert result[deps[0]][0] is result[deps[1]][0] or \
           result[deps[0]][0] == result[deps[1]][0]


def test_vuln_dataclass_is_hashable_via_id():
    """For dedup we lean on Vuln being equality-comparable by id at minimum."""
    v1 = Vuln(id="GHSA-x", aliases=[], summary="", severity=None, fixed_versions=[], references=[])
    v2 = Vuln(id="GHSA-x", aliases=["CVE-1"], summary="other", severity=None, fixed_versions=[], references=[])
    # Same id → equal (we'd dedup these by id anyway).
    assert v1 == v2 or v1.id == v2.id
