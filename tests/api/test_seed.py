"""The seed scripts fill SQL rows and vectors, and running them twice changes nothing."""

import json

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import CollectionItem, InventoryItem, KnowledgeArticle, Record, Reservation, User, WantlistItem
from scripts.knowledge_files import load_articles
from scripts.seed_catalog import DATA, seed_catalog
from scripts.seed_knowledge import seed_knowledge
from tests.api.conftest import count_vectors

RECORDS = json.loads((DATA / 'records.json').read_text(encoding='utf-8'))
N_COPIES = sum(len(r['copies']) for r in RECORDS)


async def count(model, *where) -> int:
    async with SessionLocal() as db:
        return await db.scalar(select(func.count()).select_from(model).where(*where))


async def test_seed_catalog_fills_sql_and_vectors_and_is_idempotent():
    first = await seed_catalog()
    assert first['releases added'] == len(RECORDS) and first['copies added'] == N_COPIES
    assert first['demo users added'] == 3 and first['reservations added'] == 3
    assert first['wantlist matches'] >= 1                      # Black Byrd for the jazz fan
    assert len(await count_vectors('records')) == len(RECORDS)

    # Reserved copies have real, active reservations.
    assert await count(Reservation, Reservation.status == 'active') == 3
    assert await count(InventoryItem, InventoryItem.status == 'reserved') == 3
    # Hand-typed collection entries were linked to the catalog by normalized names.
    async with SessionLocal() as db:
        bends = await db.scalar(select(CollectionItem).where(CollectionItem.title_norm == 'bends'))
        assert bends.record_id is not None and bends.title == 'The Bends'   # catalog spelling

    second = await seed_catalog()
    assert second['releases added'] == second['demo users added'] == second['reservations added'] == 0
    assert await count(Record) == len(RECORDS) and await count(User) == 3
    assert len(await count_vectors('records')) == len(RECORDS)


async def test_seed_catalog_reset_and_vectors_only():
    await seed_catalog()
    reset = await seed_catalog(reset=True)
    assert reset['releases added'] == len(RECORDS) and await count(Record) == len(RECORDS)
    assert len(await count_vectors('records')) == len(RECORDS)

    only = await seed_catalog(vectors_only=True)
    assert only['release vectors written'] == len(RECORDS) and await count(Record) == len(RECORDS)


async def test_seed_catalog_keeps_non_demo_users():
    async with SessionLocal() as db:
        db.add(User(email='real.customer@example.org', full_name='x', hashed_password='x'))
        await db.commit()
    await seed_catalog(reset=True)
    assert await count(User, User.email == 'real.customer@example.org') == 1
    assert await count(WantlistItem) == 4


async def test_seed_knowledge_fills_and_is_idempotent():
    n = len(load_articles())
    first = await seed_knowledge()
    assert first['added'] == n and first['chunks written'] == first['chunks in DB'] > n
    assert len(await count_vectors('knowledge')) == first['chunks in DB']

    second = await seed_knowledge()
    assert second['unchanged'] == n and second['chunks written'] == 0
    assert await count(KnowledgeArticle) == n
    assert len(await count_vectors('knowledge')) == first['chunks in DB']
