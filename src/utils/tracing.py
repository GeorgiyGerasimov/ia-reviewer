"""Langfuse tracing for the LangGraph pipeline.

Langfuse v4 split auth (Langfuse client) and the LangChain integration
(CallbackHandler): we initialize the client once with the configured keys,
then return a CallbackHandler that LangChain threads through every nested
model invocation. v2/v3 took the keys directly on the CallbackHandler — we
keep a fallback so this module survives a downgrade.

Tracing is OPTIONAL — if either key is missing, the package isn't installed,
or init fails, callers get `None` and the app runs without observability.
Tracing must never bring the service down.
"""

from src.utils.config import settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

try:
    # langfuse >= 4.x
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler
except ImportError:  # pragma: no cover
    try:
        # langfuse 2.x / 3.x fallback
        from langfuse.callback import CallbackHandler  # type: ignore

        Langfuse = None  # type: ignore
    except ImportError:
        Langfuse = None  # type: ignore
        CallbackHandler = None  # type: ignore


def get_langfuse_callback():
    """Return a configured CallbackHandler or None.

    Both `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` must be set;
    partial config is treated as 'tracing disabled'.
    """
    if not (settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
        return None
    if CallbackHandler is None:
        logger.warning("LANGFUSE_* keys are set but the langfuse package is not importable")
        return None
    try:
        if Langfuse is not None:
            # v4+ — initialize the client; CallbackHandler picks it up via
            # the module-level singleton.
            Langfuse(
                public_key=settings.LANGFUSE_PUBLIC_KEY,
                secret_key=settings.LANGFUSE_SECRET_KEY,
                host=settings.LANGFUSE_HOST,
            )
            return CallbackHandler()
        # v2/v3 — keys passed directly to the handler.
        return CallbackHandler(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            host=settings.LANGFUSE_HOST,
        )
    except Exception as e:
        logger.warning("Failed to initialize Langfuse — tracing disabled: %s", e)
        return None
