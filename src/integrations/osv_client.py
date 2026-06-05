"""Async client for OSV.dev — the de-facto vulnerability database for
open-source language ecosystems (npm, PyPI, Maven, Go, RubyGems, crates,
NuGet, Packagist, …).

Why OSV: free, no auth, no rate limits, aggregates 19+ upstream sources
(GitHub Advisory DB, PyPA, Go Vuln DB, RustSec, NVD conversions, …).
Used as the primary data source by OSV-Scanner, Trivy, Renovate,
pip-audit, Dependency-Track, dep-scan, GUAC.

This client wraps two endpoints:
  - POST /v1/querybatch — up to 1000 queries per call, response order
    guaranteed to match input order. Returns per-query summaries
    `{id, modified}` only.
  - GET  /v1/vulns/{id} — full advisory record (affected ranges,
    references, severity, aliases).

`query(deps)` chains both: batched IDs first, then deduped detail
lookups by id. Result is a stable mapping `{Dep: [Vuln, ...]}` you can
feed into a reviewer prompt as grounded vulnerability context.
"""

from dataclasses import dataclass, field

import httpx

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Langfuse v4 `@observe` — no-op when the package is missing or the
# client wasn't initialised. We use it so OSV.dev calls appear as
# child spans under the surrounding `dependency_scan` span in the
# Langfuse trace view.
try:
    from langfuse import observe as _observe
except ImportError:  # pragma: no cover
    def _observe(*_args, **_kwargs):
        def decorator(fn):
            return fn
        return decorator


OSV_BASE_URL = "https://api.osv.dev"
_BATCH_SIZE = 1000
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


@dataclass(frozen=True)
class Dep:
    """A concrete dependency: package coordinates that can be queried.

    Frozen so it can serve as a dict key. `ecosystem` follows OSV's
    canonical names (`npm`, `PyPI`, `Maven`, `Go`, `RubyGems`,
    `crates.io`, `NuGet`, `Packagist`).
    """

    name: str
    ecosystem: str
    version: str


@dataclass
class Vuln:
    """A normalized advisory ready to feed into a reviewer's prompt or
    a report. `id` is the canonical OSV id (often `GHSA-…`, sometimes
    `PYSEC-…` / `CVE-…`); `aliases` cross-references the other id sets."""

    id: str
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    severity: str | None = None
    fixed_versions: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        # Equality by id alone — same advisory across deps must dedup.
        return isinstance(other, Vuln) and other.id == self.id

    def __hash__(self) -> int:
        return hash(self.id)


class OSVClient:
    """Wraps OSV /v1/querybatch + /v1/vulns/{id}.

    HTTP/1.1 is fine for our scale — the 32MiB HTTP/1.1 response-body
    cap only matters on very large npm batches (thousands of deps);
    individual lockfiles almost never come close. If that changes,
    install `httpx[http2]` and flip the client to `http2=True`.

    Optional `http_client` injection: pass a shared `httpx.AsyncClient`
    (lifespan-managed) to reuse its connection pool across every
    review. Without it `query()` opens and closes its own per call —
    fine for tests, wasteful in production. Either way the caller owns
    the lifecycle of the client they passed in.
    """

    def __init__(
        self,
        base_url: str = OSV_BASE_URL,
        *,
        http_client: httpx.AsyncClient | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client

    @_observe(name="osv.query", as_type="tool")
    async def query(self, deps: list[Dep]) -> dict[Dep, list[Vuln]]:
        """For every `Dep`, return its known vulnerabilities (empty list
        when clean). Preserves the `deps` order in the response.

        Internally:
          1. Slice `deps` into 1000-sized batches.
          2. POST each batch → collect `{id, modified}` summaries.
          3. Dedupe ids globally, fetch each full record once.
          4. Map summaries back to `Vuln` objects by id.
        """
        if not deps:
            return {}

        result: dict[Dep, list[Vuln]] = {dep: [] for dep in deps}
        # Two-pass: collect (dep, vuln_id) pairs first, fetch details after.
        dep_to_ids: dict[Dep, list[str]] = {dep: [] for dep in deps}

        async with self._acquire_client() as client:
            # Pass 1: batched queries.
            for chunk in _chunked(deps, _BATCH_SIZE):
                payload = {
                    "queries": [
                        {
                            "package": {"name": d.name, "ecosystem": d.ecosystem},
                            "version": d.version,
                        }
                        for d in chunk
                    ]
                }
                response = await client.post(
                    f"{self._base_url}/v1/querybatch", json=payload
                )
                response.raise_for_status()
                results = response.json().get("results", [])
                for dep, entry in zip(chunk, results, strict=False):
                    for vuln_summary in (entry or {}).get("vulns") or []:
                        dep_to_ids[dep].append(vuln_summary["id"])

            # Pass 2: dedup ids globally, fetch each detail once.
            # Per-id error isolation: a single bad advisory (404 / 5xx /
            # network error) drops to None and gets filtered out below
            # — every OTHER vuln we managed to fetch still surfaces.
            # Before this isolation a single broken id from OSV aborted
            # the whole scan and the reviewer reported "no findings".
            unique_ids = {vid for ids in dep_to_ids.values() for vid in ids}
            details: dict[str, Vuln | None] = {}
            for vid in unique_ids:
                details[vid] = await self._fetch_detail_safely(client, vid)

        # Stitch deps → Vulns using the dedup'd detail map.
        for dep, ids in dep_to_ids.items():
            # Preserve order of first appearance, drop duplicates inside a single dep.
            seen: set[str] = set()
            for vid in ids:
                if vid in seen:
                    continue
                seen.add(vid)
                vuln = details.get(vid)
                if vuln is not None:
                    result[dep].append(vuln)
        return result

    def _acquire_client(self):
        """Return an async context manager yielding an `httpx.AsyncClient`.

        Injected client → contextlib.nullcontext wrapper (we never close
        someone else's client). No injection → open a fresh client whose
        lifetime is exactly this `query` call.
        """
        if self._http_client is not None:
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _shared():
                yield self._http_client

            return _shared()
        return httpx.AsyncClient(timeout=_TIMEOUT)

    async def _fetch_detail_safely(
        self, client: httpx.AsyncClient, vuln_id: str
    ) -> Vuln | None:
        """Return the normalised `Vuln` for `vuln_id`, or `None` on any
        recoverable failure (HTTP 4xx/5xx, network error, malformed
        JSON). The caller treats `None` as "drop this advisory" — one
        broken id never blocks the rest of the batch."""
        try:
            response = await client.get(f"{self._base_url}/v1/vulns/{vuln_id}")
            response.raise_for_status()
        except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
            logger.warning(
                "osv detail fetch failed for %s: %s — dropping this advisory",
                vuln_id,
                exc,
            )
            return None
        try:
            return _vuln_from_payload(response.json())
        except (ValueError, KeyError) as exc:
            logger.warning(
                "osv detail malformed payload for %s: %s — dropping",
                vuln_id,
                exc,
            )
            return None

    async def _fetch_detail(self, client: httpx.AsyncClient, vuln_id: str) -> Vuln:
        """Legacy entry-point kept for any external callers / tests; new
        code should use `_fetch_detail_safely`. Behaviour unchanged
        (still raises on errors)."""
        response = await client.get(f"{self._base_url}/v1/vulns/{vuln_id}")
        response.raise_for_status()
        return _vuln_from_payload(response.json())


def _chunked(items: list[Dep], size: int) -> list[list[Dep]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _vuln_from_payload(data: dict) -> Vuln:
    """Normalize an OSV /v1/vulns/{id} response into our `Vuln`.

    Extracts `fixed` versions from `affected[].ranges[].events`. Each
    range describes a contiguous affected window via ordered events:
    `introduced`, `fixed`, `last_affected`, `limit`. We surface `fixed`
    values so the reviewer can include "upgrade to ≥ X.Y.Z" advice.
    """
    fixed: list[str] = []
    for affected in data.get("affected") or []:
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                if "fixed" in event:
                    fixed.append(event["fixed"])

    references = [
        ref["url"] for ref in (data.get("references") or []) if "url" in ref
    ]

    # OSV's severity is a list of typed scores (CVSS_V3 / CVSS_V2 / etc.);
    # surface the first CVSS_V3 string if present, otherwise the first one.
    severity: str | None = None
    severity_entries = data.get("severity") or []
    for entry in severity_entries:
        if entry.get("type") == "CVSS_V3" and entry.get("score"):
            severity = entry["score"]
            break
    if severity is None and severity_entries:
        severity = severity_entries[0].get("score")

    return Vuln(
        id=data["id"],
        aliases=list(data.get("aliases") or []),
        summary=data.get("summary") or "",
        severity=severity,
        # Drop dupes while preserving order.
        fixed_versions=list(dict.fromkeys(fixed)),
        references=references,
    )
