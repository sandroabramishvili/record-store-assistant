"""Fixtures for API tests: real FastAPI app, real Postgres (the *_test database).

Needs the Docker database running:  docker compose up -d db
"""

import asyncio
from dataclasses import dataclass

import asyncpg
import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from langchain_core.embeddings import DeterministicFakeEmbedding
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.ai import vectorstores
from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import User
from tests.conftest import ROOT

PASSWORD = 'vinyl-lover-1'
_HASH = hash_password(PASSWORD)  # hash once: Argon2 is slow on purpose
_TABLES = ', '.join(t.name for t in Base.metadata.sorted_tables)


# ---------- once per test run ----------


def _create_database_if_missing(url) -> None:
    async def run() -> None:
        try:
            conn = await asyncpg.connect(
                user=url.username, password=url.password, host=url.host, port=url.port, database='postgres'
            )
        except OSError as e:
            raise RuntimeError('Postgres is not reachable. Start it with: docker compose up -d db') from e
        try:
            if not await conn.fetchval('SELECT 1 FROM pg_database WHERE datname = $1', url.database):
                await conn.execute(f'CREATE DATABASE "{url.database}"')
        finally:
            await conn.close()

    asyncio.run(run())


@pytest.fixture(scope='session', autouse=True)
def test_database() -> None:
    """Create record_store_test if needed and bring it to the latest migration."""
    url = make_url(settings.database_url)
    assert url.database.endswith('_test'), 'API tests must run against a *_test database'
    _create_database_if_missing(url)
    # The real migrations, not create_all: this also tests the migrations themselves.
    command.upgrade(Config(str(ROOT / 'alembic.ini')), 'head')
    # Never call Gemini in tests (spec 12): same text → same vector, no network, no cost.
    vectorstores.use_embeddings(DeterministicFakeEmbedding(size=settings.embedding_dimensions))


# ---------- per test ----------


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    """Every test starts with empty tables (ids from 1). Data is left after the test for debugging."""
    async with engine.begin() as conn:
        await conn.execute(text(f'TRUNCATE {_TABLES} RESTART IDENTITY CASCADE'))
        # PGVector's tables (they exist once any test has used a vector store).
        if await conn.scalar(text("SELECT to_regclass('langchain_pg_collection')")):
            await conn.execute(text('TRUNCATE langchain_pg_collection CASCADE'))
        # LangGraph's checkpoint tables (exist once a chat test has opened the checkpointer).
        if await conn.scalar(text("SELECT to_regclass('checkpoints')")):
            await conn.execute(text('TRUNCATE checkpoints, checkpoint_blobs, checkpoint_writes'))
    yield
    # Each test has its own event loop; pooled connections can't be reused in the next one.
    await vectorstores.close_vector_stores()
    await engine.dispose()


async def count_vectors(collection: str, id_prefix: str = '') -> list[tuple[str, str, dict]]:
    """Rows of one PGVector collection: (id, text, metadata), ordered by id."""
    async with engine.connect() as conn:
        if not await conn.scalar(text("SELECT to_regclass('langchain_pg_embedding')")):
            return []
        rows = await conn.execute(text(
            'SELECT e.id, e.document, e.cmetadata FROM langchain_pg_embedding e '
            'JOIN langchain_pg_collection c ON c.uuid = e.collection_id '
            'WHERE c.name = :c AND e.id LIKE :p ORDER BY e.id'), {'c': collection, 'p': f'{id_prefix}%'})
        return [tuple(r) for r in rows]


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        yield c


@dataclass
class ApiUser:
    id: int
    email: str
    headers: dict[str, str]


async def _create_user(role: str, email: str) -> ApiUser:
    async with SessionLocal() as db:
        user = User(email=email, full_name=email.split('@')[0], hashed_password=_HASH, role=role)
        db.add(user)
        await db.commit()
    token = create_access_token(user.id, role)
    return ApiUser(user.id, email, {'Authorization': f'Bearer {token}'})


@pytest_asyncio.fixture
async def staff() -> ApiUser:
    return await _create_user('staff', 'staff@example.com')


@pytest_asyncio.fixture
async def customer() -> ApiUser:
    return await _create_user('customer', 'alice@example.com')


@pytest_asyncio.fixture
async def other_customer() -> ApiUser:
    return await _create_user('customer', 'bob@example.com')


@pytest_asyncio.fixture
async def make_customers():
    """Many customers at once, for concurrency tests."""
    async def _make(n: int) -> list[ApiUser]:
        return [await _create_user('customer', f'customer{i}@example.com') for i in range(n)]
    return _make


# ---------- catalog helpers (through the API, as staff) ----------


@pytest_asyncio.fixture
async def add_record(client, staff):
    async def _add(artist: str = 'Herbie Hancock', title: str = 'Head Hunters', **extra) -> int:
        body = {'artist': artist, 'title': title, 'label': 'Columbia', 'release_year': 1973,
                'original_year': 1973, 'genres': ['jazz', 'funk'], 'description': 'Test release.'} | extra
        r = await client.post('/records', headers=staff.headers, json=body)
        assert r.status_code == 201, r.text
        return r.json()['id']
    return _add


@pytest_asyncio.fixture
async def add_copy(client, staff, add_record):
    async def _add(record_id: int | None = None, price: str = '30.00', media: str = 'VG+', sleeve: str = 'VG+') -> int:
        if record_id is None:
            record_id = await add_record()
        r = await client.post('/inventory', headers=staff.headers, json={
            'record_id': record_id, 'media_condition': media, 'sleeve_condition': sleeve, 'price': price})
        assert r.status_code == 201, r.text
        return r.json()['id']
    return _add
