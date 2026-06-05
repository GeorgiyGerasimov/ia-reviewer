"""Unit tests for the Langfuse tracing helper.

The helper is defensive on purpose — observability must never bring the
service down. Missing keys, missing package, or init failures all return
`None` and let the app run untraced.
"""

from unittest.mock import MagicMock, patch

from src.utils import tracing


def test_get_langfuse_callback_returns_none_without_keys():
    with patch.object(tracing, "settings") as mock_settings:
        mock_settings.LANGFUSE_PUBLIC_KEY = ""
        mock_settings.LANGFUSE_SECRET_KEY = ""
        assert tracing.get_langfuse_callback() is None


def test_get_langfuse_callback_returns_none_when_only_one_key_set():
    """Both keys must be configured — partial config is treated as 'off'."""
    with patch.object(tracing, "settings") as mock_settings:
        mock_settings.LANGFUSE_PUBLIC_KEY = "pk-test"
        mock_settings.LANGFUSE_SECRET_KEY = ""
        assert tracing.get_langfuse_callback() is None


def test_get_langfuse_callback_returns_handler_when_keys_present():
    fake_handler = MagicMock(name="CallbackHandler instance")
    with patch.object(tracing, "settings") as mock_settings:
        mock_settings.LANGFUSE_PUBLIC_KEY = "pk-test"
        mock_settings.LANGFUSE_SECRET_KEY = "sk-test"
        mock_settings.LANGFUSE_HOST = "http://localhost:3000"
        with patch.object(tracing, "Langfuse") as mock_langfuse_cls, patch.object(
            tracing, "CallbackHandler", return_value=fake_handler
        ) as mock_handler_cls:
            result = tracing.get_langfuse_callback()
            assert result is fake_handler
            mock_langfuse_cls.assert_called_once()
            mock_handler_cls.assert_called_once()


def test_get_langfuse_callback_handles_init_failure_gracefully():
    with patch.object(tracing, "settings") as mock_settings:
        mock_settings.LANGFUSE_PUBLIC_KEY = "pk-test"
        mock_settings.LANGFUSE_SECRET_KEY = "sk-test"
        mock_settings.LANGFUSE_HOST = "http://localhost:3000"
        with patch.object(tracing, "Langfuse", side_effect=RuntimeError("auth failed")):
            # Must not propagate; tracing failure ≠ app failure.
            assert tracing.get_langfuse_callback() is None
