"""Async context manager that wraps LangGraph's PostgresSaver lifecycle.

Usage:
    async with open_checkpointer(settings.DATABASE_URL) as checkpointer:
        graph = build_review_graph(checkpointer=checkpointer)
        ...

When `database_url` is empty/None, yields None (no checkpointing). When set,
opens an `AsyncPostgresSaver`, awaits `.setup()` to create checkpoint tables
on first use, and ensures clean close on exit.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


@asynccontextmanager
async def open_checkpointer(database_url: str | None) -> AsyncIterator[AsyncPostgresSaver | None]:
    if not database_url:
        yield None
        return
    async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
        await saver.setup()
        yield saver
