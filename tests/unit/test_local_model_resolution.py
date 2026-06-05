"""Tests for the local-model resolution path in ModelFactory.

The AI Gateway mode is repurposed for any OpenAI-compatible endpoint. When
the endpoint is a local model server (e.g. vLLM, LiteLLM, llama.cpp), the
prefix-mapped Bifrost-style name (`anthropic/claude-sonnet-4-6`) won't match
anything the server exposes, so we resolve the real model id either from
`settings.AI_GATEWAY_MODEL` (explicit) or by querying `/v1/models` on the
gateway (auto-discovery).
"""

import httpx
import pytest
import respx

from src.models import factory


@pytest.fixture(autouse=True)
def _reset_factory_caches():
    """Module-level discovery cache and instance cache must not leak between tests."""
    factory._discovered_gateway_model = None
    factory.ModelFactory._instances.clear()
    yield
    factory._discovered_gateway_model = None
    factory.ModelFactory._instances.clear()


def test_settings_ai_gateway_url_default_points_to_local_box():
    """Settings DEFAULT (no .env override) points at the corp LAN box.

    Constructed with `_env_file=None` so a developer's local .env (which
    may pin a different port for live-test work) doesn't shadow the
    in-code default we're asserting on.
    """
    from src.utils.config import Settings

    s = Settings(_env_file=None)
    assert s.AI_GATEWAY_URL == "http://localhost:8001/v1"


def test_resolve_uses_explicit_ai_gateway_model_when_set(mocker):
    mocker.patch.object(factory, "settings", autospec=False)
    factory.settings.AI_GATEWAY_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"
    factory.settings.AI_GATEWAY_URL = "http://localhost:8001/v1"

    resolved = factory._resolve_gateway_model("claude-sonnet-4-6")

    # Explicit pin wins regardless of the internal alias.
    assert resolved == "Qwen/Qwen2.5-Coder-32B-Instruct"


@respx.mock
def test_resolve_discovers_first_model_from_endpoint(mocker):
    mocker.patch.object(factory, "settings", autospec=False)
    factory.settings.AI_GATEWAY_MODEL = ""
    factory.settings.AI_GATEWAY_URL = "http://localhost:8001/v1"

    respx.get("http://localhost:8001/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "Qwen/Qwen2.5-7B-Instruct", "object": "model"},
                    {"id": "meta-llama/Llama-3.1-8B", "object": "model"},
                ],
            },
        )
    )

    resolved = factory._resolve_gateway_model("claude-sonnet-4-6")
    assert resolved == "Qwen/Qwen2.5-7B-Instruct"


@respx.mock
def test_resolve_falls_back_to_prefix_mapping_when_discovery_fails(mocker):
    mocker.patch.object(factory, "settings", autospec=False)
    factory.settings.AI_GATEWAY_MODEL = ""
    factory.settings.AI_GATEWAY_URL = "http://localhost:8001/v1"

    respx.get("http://localhost:8001/v1/models").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    resolved = factory._resolve_gateway_model("claude-sonnet-4-6")

    # On discovery failure, the existing Bifrost-style mapping is the safety net.
    assert resolved == factory.GATEWAY_MODEL_NAMES["claude-sonnet-4-6"]


@respx.mock
def test_resolve_caches_discovery_result_across_calls(mocker):
    mocker.patch.object(factory, "settings", autospec=False)
    factory.settings.AI_GATEWAY_MODEL = ""
    factory.settings.AI_GATEWAY_URL = "http://localhost:8001/v1"

    route = respx.get("http://localhost:8001/v1/models").mock(
        return_value=httpx.Response(
            200, json={"object": "list", "data": [{"id": "Qwen/Qwen2.5-7B", "object": "model"}]}
        )
    )

    first = factory._resolve_gateway_model("claude-sonnet-4-6")
    second = factory._resolve_gateway_model("claude-opus-4-7")  # different internal name

    assert first == second == "Qwen/Qwen2.5-7B"
    # Network must be hit exactly once — subsequent resolutions use the cache.
    assert route.call_count == 1
