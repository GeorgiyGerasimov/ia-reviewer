from unittest.mock import AsyncMock, MagicMock, patch

from src.utils.checkpointer import open_checkpointer


async def test_open_checkpointer_yields_none_when_url_empty():
    async with open_checkpointer("") as cp:
        assert cp is None


async def test_open_checkpointer_yields_none_when_url_none():
    async with open_checkpointer(None) as cp:
        assert cp is None


async def test_open_checkpointer_opens_async_postgres_saver():
    fake_saver = AsyncMock()
    fake_saver.setup = AsyncMock()

    fake_ctx = MagicMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_saver)
    fake_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "src.utils.checkpointer.AsyncPostgresSaver.from_conn_string",
        return_value=fake_ctx,
    ) as mock_from_conn:
        async with open_checkpointer("postgresql://x") as cp:
            assert cp is fake_saver

        mock_from_conn.assert_called_once_with("postgresql://x")
        fake_saver.setup.assert_awaited_once()
        fake_ctx.__aenter__.assert_awaited_once()
        fake_ctx.__aexit__.assert_awaited_once()
