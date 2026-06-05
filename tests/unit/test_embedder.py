"""Embedder — POSTs to an OpenAI-compatible /embeddings endpoint.

Contract:
- `Embedder(base_url, model, api_key=, dim=, http_client=).embed(text)` returns
  `list[float]` of length `dim` on success, or `None` on ANY failure mode.
  Callers treat `None` as "RAG disabled for this call" — the surrounding
  review path must continue.
- Empty `model` (= RAG disabled by config) returns `None` without an HTTP call.
- Empty / whitespace `text` returns `None` without an HTTP call.
- Network error / 4xx / 5xx / malformed JSON / wrong-dim response → `None`.
- `http_client` injection lets callers share an `httpx.AsyncClient` across
  the embedder and other integrations.

Wire format follows OpenAI's `/v1/embeddings`:
  POST  → {"model": "...", "input": "..."}
  resp  → {"data": [{"embedding": [..floats..]}]}
"""

import httpx
import respx

GATEWAY = "http://gateway.local/v1"


def _make_embedder(*, dim: int = 4, model: str = "bge-large", api_key: str = "key"):
    from src.integrations.embedder import Embedder

    return Embedder(
        base_url=GATEWAY,
        model=model,
        api_key=api_key,
        dim=dim,
    )


@respx.mock
async def test_embed_success_returns_floats():
    """Happy path — OpenAI-shape response decoded into a list[float]."""
    emb = _make_embedder(dim=4)
    respx.post(f"{GATEWAY}/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]},
        )
    )

    out = await emb.embed("some finding text")

    assert out == [0.1, 0.2, 0.3, 0.4]


@respx.mock
async def test_embed_sends_model_and_input_in_body():
    """Request body must carry the configured model + the input text."""
    emb = _make_embedder(model="bge-large", dim=4)
    route = respx.post(f"{GATEWAY}/embeddings").mock(
        return_value=httpx.Response(
            200, json={"data": [{"embedding": [0.0] * 4}]},
        )
    )

    await emb.embed("payload")

    assert route.called
    sent = route.calls[0].request.read()
    import json as _json
    body = _json.loads(sent)
    assert body["model"] == "bge-large"
    assert body["input"] == "payload"


async def test_embed_empty_model_skips_http_and_returns_none():
    """`model=""` means RAG is disabled at config level — short-circuit."""
    emb = _make_embedder(model="")

    # No respx context — any HTTP call would fail outright. None is the contract.
    out = await emb.embed("anything")

    assert out is None


async def test_embed_blank_text_skips_http_and_returns_none():
    """Empty / whitespace input never reaches the network."""
    emb = _make_embedder()

    assert await emb.embed("") is None
    assert await emb.embed("   \n  ") is None


@respx.mock
async def test_embed_returns_none_on_404():
    """Gateway w/o /embeddings → graceful None, not exception."""
    emb = _make_embedder()
    respx.post(f"{GATEWAY}/embeddings").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )

    assert await emb.embed("hi") is None


@respx.mock
async def test_embed_returns_none_on_5xx():
    emb = _make_embedder()
    respx.post(f"{GATEWAY}/embeddings").mock(
        return_value=httpx.Response(503, text="gateway down")
    )

    assert await emb.embed("hi") is None


@respx.mock
async def test_embed_returns_none_on_network_error():
    emb = _make_embedder()
    respx.post(f"{GATEWAY}/embeddings").mock(
        side_effect=httpx.ConnectError("boom")
    )

    assert await emb.embed("hi") is None


@respx.mock
async def test_embed_returns_none_on_malformed_payload():
    """No `data` key, or `data[0]` lacks `embedding`, or non-list embedding."""
    emb = _make_embedder()
    respx.post(f"{GATEWAY}/embeddings").mock(
        return_value=httpx.Response(200, json={"unexpected": True})
    )

    assert await emb.embed("hi") is None


@respx.mock
async def test_embed_returns_none_on_dim_mismatch():
    """Wrong-dim response means the model was swapped server-side and the
    pgvector column will reject the insert. Fail closed: None.
    """
    emb = _make_embedder(dim=4)
    respx.post(f"{GATEWAY}/embeddings").mock(
        return_value=httpx.Response(
            200, json={"data": [{"embedding": [0.1, 0.2]}]}  # dim=2, expected 4
        )
    )

    assert await emb.embed("hi") is None


@respx.mock
async def test_embed_includes_authorization_header_when_api_key_set():
    """API key gets sent as Bearer — gateway auth still works."""
    emb = _make_embedder(api_key="secret-key", dim=2)
    route = respx.post(f"{GATEWAY}/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.0, 0.0]}]})
    )

    await emb.embed("hi")

    assert route.calls[0].request.headers.get("Authorization") == "Bearer secret-key"


@respx.mock
async def test_embed_omits_authorization_when_api_key_empty():
    """No key = no Authorization header (gateway may treat empty Bearer
    as a hard failure)."""
    emb = _make_embedder(api_key="", dim=2)
    route = respx.post(f"{GATEWAY}/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.0, 0.0]}]})
    )

    await emb.embed("hi")

    assert "Authorization" not in route.calls[0].request.headers


@respx.mock
async def test_embed_trims_base_url_trailing_slash():
    """`http://x/v1/` and `http://x/v1` must hit the same URL."""
    from src.integrations.embedder import Embedder

    emb = Embedder(base_url=f"{GATEWAY}/", model="m", api_key="k", dim=2)
    route = respx.post(f"{GATEWAY}/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.0, 0.0]}]})
    )

    out = await emb.embed("hi")

    assert out == [0.0, 0.0]
    assert route.called
