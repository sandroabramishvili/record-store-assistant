"""Loaded by pytest before any test module, and therefore before `app` is imported.

No Gemini: GOOGLE_API_KEY is blanked for the whole run (see below).

Safety first: point DATABASE_URL at a separate TEST database, so no test can ever
touch the development data. Settings reads environment variables before `.env`,
so the whole app (routers, services, SessionLocal) uses the test database.

Test DB = TEST_DATABASE_URL if set, otherwise DATABASE_URL with `_test` appended
to the database name (record_store → record_store_test).
"""

import asyncio
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def _test_database_url() -> str | None:
    if os.environ.get('TEST_DATABASE_URL'):
        return os.environ['TEST_DATABASE_URL']
    base = os.environ.get('DATABASE_URL') or dotenv_values(ROOT / '.env').get('DATABASE_URL')
    if not base:
        return None  # unit tests don't need a database
    url = make_url(base)
    if url.database.endswith('_test'):
        return base
    return url.set(database=f'{url.database}_test').render_as_string(hide_password=False)


# No real LLM calls in tests. Blank the Gemini key for the whole run (an empty
# environment variable beats the real key in .env), so a test that accidentally builds a
# real Gemini model fails with "GOOGLE_API_KEY is not set" instead of silently calling it.
os.environ['GOOGLE_API_KEY'] = ''
# Same for LangSmith: test runs must not end up as traces in the real project.
os.environ['LANGSMITH_TRACING'] = 'false'

_url = _test_database_url()
if _url is not None:
    if not make_url(_url).database.endswith('_test'):
        raise RuntimeError('Refusing to run tests: the test database name must end with "_test".')
    os.environ['DATABASE_URL'] = _url


def pytest_asyncio_loop_factories(config, item):
    """Windows: run async tests on a SelectorEventLoop (psycopg can't use the default
    ProactorEventLoop). Elsewhere: None = pytest-asyncio's normal loop."""
    if sys.platform == 'win32':
        return {'selector': asyncio.SelectorEventLoop}
    return None
