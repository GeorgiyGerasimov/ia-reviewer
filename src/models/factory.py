"""
ModelFactory: gets LangChain chat models, with two modes.

- USE_AI_GATEWAY=True (default): all providers go through an OpenAI-compatible
  endpoint (Bifrost, LiteLLM, vLLM, llama.cpp, a local serving box, …). We use
  ChatOpenAI with base_url=AI_GATEWAY_URL and resolve the actual model name via:
    1. `settings.AI_GATEWAY_MODEL` if set (explicit pin)
    2. Auto-discovery — GET `<AI_GATEWAY_URL>/models`, take the first `data[].id`
    3. Bifrost-style mapping `provider/internal-name` as last-resort fallback
- USE_AI_GATEWAY=False: legacy direct mode — native LangChain clients per
  provider (Anthropic / OpenAI / Google).

Internal model names ("claude-opus-4-7", "gpt-4o", ...) stay stable across
modes so per-agent overrides keep working even when the gateway swaps out.
"""

import httpx
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

SUPPORTED_MODELS = {
    "claude-opus-4-7": lambda: ChatAnthropic(model="claude-opus-4-7"),
    "claude-sonnet-4-6": lambda: ChatAnthropic(model="claude-sonnet-4-6"),
    "claude-haiku-4-5": lambda: ChatAnthropic(model="claude-haiku-4-5-20251001"),
    "gpt-4o": lambda: ChatOpenAI(model="gpt-4o"),
    "gpt-4o-mini": lambda: ChatOpenAI(model="gpt-4o-mini"),
    "gemini-2.0-flash": lambda: ChatGoogleGenerativeAI(model="gemini-2.0-flash"),
}

GATEWAY_MODEL_NAMES = {
    "claude-opus-4-7": "anthropic/claude-opus-4-7",
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4-6",
    "claude-haiku-4-5": "anthropic/claude-haiku-4-5",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gemini-2.0-flash": "google/gemini-2.0-flash",
}

_DISCOVERY_TIMEOUT_SECONDS = 5.0

# Cache the first successful auto-discovery so subsequent calls don't hit the
# network. None means 'not yet attempted or last attempt failed' — failures
# fall through to the prefix-mapping fallback each time, so a transiently
# down server doesn't permanently disable tracing-by-real-model-name.
_discovered_gateway_model: str | None = None


def _discover_gateway_model_id(base_url: str) -> str | None:
    """Sync GET `<base_url>/models` and return the first `data[].id`, or None.

    Sync (not async) because ModelFactory.get is sync and called from
    constructor paths. ChatOpenAI itself defers all real I/O to ainvoke, so
    a single sync request on first model-build is acceptable.
    """
    global _discovered_gateway_model
    if _discovered_gateway_model is not None:
        return _discovered_gateway_model
    url = f"{base_url.rstrip('/')}/models"
    try:
        response = httpx.get(url, timeout=_DISCOVERY_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json().get("data") or []
        if data and isinstance(data, list):
            model_id = data[0].get("id") if isinstance(data[0], dict) else None
            if model_id:
                _discovered_gateway_model = model_id
                logger.info("Discovered gateway model: %s from %s", model_id, url)
                return model_id
        logger.warning("Gateway /models returned no usable entries: %s", url)
    except Exception as e:
        logger.warning("Gateway model discovery failed at %s: %s", url, e)
    return None


def _resolve_gateway_model(internal_name: str) -> str:
    """Pick the model name to pass to ChatOpenAI for the gateway request.

    Priority:
      1. `settings.AI_GATEWAY_MODEL` — explicit pin
      2. Auto-discovery cache (first id from `/v1/models`)
      3. Bifrost-style `provider/internal-name` mapping
    """
    if settings.AI_GATEWAY_MODEL:
        return settings.AI_GATEWAY_MODEL
    discovered = _discover_gateway_model_id(settings.AI_GATEWAY_URL)
    if discovered:
        return discovered
    return GATEWAY_MODEL_NAMES.get(internal_name, internal_name)


class ModelFactory:
    _instances: dict[str, BaseChatModel] = {}

    @classmethod
    def get(cls, model_name: str) -> BaseChatModel:
        if model_name in cls._instances:
            return cls._instances[model_name]
        instance = cls._build_via_gateway(model_name) if settings.USE_AI_GATEWAY else cls._build_direct(model_name)
        cls._instances[model_name] = instance
        return instance

    @classmethod
    def _build_via_gateway(cls, model_name: str) -> BaseChatModel:
        return ChatOpenAI(
            model=_resolve_gateway_model(model_name),
            base_url=settings.AI_GATEWAY_URL,
            api_key=settings.AI_GATEWAY_API_KEY or "no-key",
        )

    @classmethod
    def _build_direct(cls, model_name: str) -> BaseChatModel:
        if model_name not in SUPPORTED_MODELS:
            raise ValueError(f"Unknown model: {model_name}. Supported: {list(SUPPORTED_MODELS)}")
        return SUPPORTED_MODELS[model_name]()
