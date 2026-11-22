"""Event loop choice, for Windows only.

psycopg 3 (used by PGVector and, in Phase 11, by the LangGraph checkpointer) cannot run
in async mode on Windows' default ProactorEventLoop; it needs a SelectorEventLoop.
On Linux/macOS (and in Docker) the default loop already works and nothing changes.

- uvicorn:  `--reload` already uses a SelectorEventLoop. Without --reload:
            uvicorn app.main:app --loop app.loop:selector_loop_factory
- scripts:  app.loop.run(main())  instead of  asyncio.run(main())
- pytest:   the pytest_asyncio_loop_factories hook in tests/conftest.py
"""

import asyncio
import sys
from collections.abc import Callable, Coroutine
from typing import Any


def selector_loop_factory(use_subprocess: bool = False) -> Callable[[], asyncio.AbstractEventLoop]:
    """uvicorn's --loop hook: return the loop class to use (Selector on every platform)."""
    return asyncio.SelectorEventLoop


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """asyncio.run with a loop psycopg can use on Windows."""
    if sys.platform == 'win32':
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)
