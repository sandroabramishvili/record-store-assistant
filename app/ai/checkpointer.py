"""The LangGraph checkpointer in Postgres.

After every node, LangGraph saves the graph's state for the chat's thread here. That is
what makes the chat remember "the second one" between HTTP requests, and what lets a
confirmation survive an API restart: the paused graph is in the database, not in memory.

A connection POOL, not `from_conn_string` (one connection): several chats run at once.
The three connection settings are required by AsyncPostgresSaver:
- autocommit       each checkpoint write is committed immediately
- prepare_threshold=0  no server-side prepared statements (safe behind poolers like PgBouncer)
- dict_row         the saver reads rows as dicts

Windows: psycopg async needs a SelectorEventLoop (see app/loop.py).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

# Tables the saver creates (and owns). Alembic ignores them (include_name in migrations/env.py).
CHECKPOINT_TABLES = ('checkpoints', 'checkpoint_blobs', 'checkpoint_writes', 'checkpoint_migrations')


@asynccontextmanager
async def open_checkpointer(max_size: int = 10) -> AsyncIterator[AsyncPostgresSaver]:
    """Open a pool, create/upgrade the checkpoint tables (idempotent), yield the saver, close the pool."""
    pool = AsyncConnectionPool(
        conninfo=settings.checkpointer_url,
        min_size=1,                                   # grows on demand up to max_size
        max_size=max_size,
        open=False,                                   # opened by `async with` (opening in __init__ is deprecated)
        kwargs={'autocommit': True, 'prepare_threshold': 0, 'row_factory': dict_row},
    )
    async with pool:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        yield checkpointer
