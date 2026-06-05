"""Embeddings client for the RAG-by-past-findings path.

Targets an OpenAI-compatible `/embeddings` endpoint (same shape as
`/v1/chat/completions` — Bifrost, LiteLLM, vLLM, llama.cpp, OpenAI
itself, all expose the same wire). Returns `list[float]` of length
`dim` on success, or `None` on **any** failure mode (network, 4xx, 5xx,
malformed JSON, dim mismatch). The surrounding RAG path is expected
to treat `None` as "no retrieval this run" and continue the review
without the past-findings splice.

Why fail-soft instead of raising:
  * Embeddings are an enhancement, not a hard dependency. If the local
    gateway server doesn't ship `/embeddings` (common with single-model
    boxes), the review must still produce a report.
  * The pgvector column has a fixed dim; if the server quietly swapped
    in a different-dim model, inserting the embedding would corrupt the
    table on its way in. Dim-mismatch → None blocks that path.

Tests live in `tests/unit/test_embedder.py`.
"""

from __future__ import annotations

import httpx

from src.utils.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 10.0


class Embedder:
    """Async embedder against an OpenAI-compatible `/embeddings` endpoint.

    Construct once at app-startup and pass into agents that need
    retrieval. `aclose()` closes the underlying httpx client when the
    app shuts down.

    Args:
      base_url:    Gateway base URL, e.g. ``http://10.30.1.14:8001/v1``.
                   Trailing slashes are tolerated.
      model:       Embedding model id on the gateway. Empty string =
                   embedder disabled (every `embed` returns None
                   without an HTTP call).
      api_key:     Optional Bearer token. Empty = no Authorization
                   header sent.
      dim:         Expected embedding dimensionality. Must match the
                   pgvector column dim in `review_findings.embedding`.
                   Wrong-dim server responses are treated as failure.
      timeout:     Per-call HTTP timeout (seconds).
      http_client: Optional injected `httpx.AsyncClient`. Useful for
                   sharing a connection pool across integrations or for
                   tests; if None, one is created lazily and owned by
                   this Embedder.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        dim: int = 1024,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._dim = dim
        self._timeout = timeout
        self._client = http_client
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        """Close the owned httpx client. No-op when one was injected."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def embed(self, text: str) -> list[float] | None:
        """Return an embedding for `text`, or `None` on any failure.

        See module docstring for the fail-soft contract. The result
        list has length `self._dim` on success.
        """
        if not self._model:
            # Disabled-by-config — silent, no warning spam.
            return None
        if not text or not text.strip():
            return None

        url = f"{self._base_url}/embeddings"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            client = self._get_client()
            response = await client.post(
                url,
                json={"model": self._model, "input": text},
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as e:
            logger.warning("embedder: network error against %s: %s", url, e)
            return None

        if response.status_code >= 400:
            logger.warning(
                "embedder: %s returned HTTP %d (body=%.120r)",
                url,
                response.status_code,
                response.text,
            )
            return None

        try:
            payload = response.json()
        except ValueError as e:
            logger.warning("embedder: %s returned non-JSON: %s", url, e)
            return None

        embedding = _extract_embedding(payload)
        if embedding is None:
            logger.warning(
                "embedder: %s returned unexpected payload: %.200r",
                url,
                payload,
            )
            return None

        if len(embedding) != self._dim:
            logger.warning(
                "embedder: dim mismatch from %s — expected %d, got %d. "
                "Model swapped server-side? Failing soft.",
                url,
                self._dim,
                len(embedding),
            )
            return None

        return embedding

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client


def _extract_embedding(payload: object) -> list[float] | None:
    """Pull `data[0].embedding` out of an OpenAI-shape response.

    Defensive — returns None if anything in the path is missing or
    wrong-typed.
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if not isinstance(first, dict):
        return None
    embedding = first.get("embedding")
    if not isinstance(embedding, list):
        return None
    # Coerce ints to floats so callers can rely on the type. Reject
    # anything non-numeric so the pg insert won't choke.
    out: list[float] = []
    for v in embedding:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
        else:
            return None
    return out
