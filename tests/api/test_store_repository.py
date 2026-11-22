"""StoreRepository on the seeded test catalog (fake embeddings)."""

import json

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.ai.vectorstores import get_knowledge_store, get_record_store
from app.db import SessionLocal
from app.models import SearchLog, User
from app.repositories.store import StoreRepository
from app.services.availability import filter_available
from app.services.reservations import ReservationConflict, ReservationLimitReached
from scripts.seed_catalog import seed_catalog
from scripts.seed_knowledge import seed_knowledge


@pytest_asyncio.fixture
async def repo():
    await seed_catalog()
    return StoreRepository(SessionLocal, get_record_store(), get_knowledge_store())


async def user_id(email: str) -> int:
    async with SessionLocal() as db:
        return await db.scalar(select(User.id).where(User.email == email))


async def test_load_context(repo):
    ctx = await repo.load_context(await user_id('jazz.fan@example.com'))
    assert ctx.profile == {'favorite_genres': ['jazz', 'funk', 'soul', 'jazz-funk', 'hard bop'],
                           'disliked_genres': ['hip-hop'], 'budget_cap': '60.00', 'min_condition': 'VG+'}
    assert ('fleetwood mac', 'rumours') in ctx.owned_keys and len(ctx.owned_record_ids) == 3   # Bill Evans isn't in the catalog
    assert ctx.wantlist_artists == ['alice coltrane', 'donald byrd']
    assert (await repo.load_context(999999)).profile == {}                                   # unknown user: empty


async def test_search_plus_filter_is_the_fleetwood_mac_demo(repo):
    ctx = await repo.load_context(await user_id('jazz.fan@example.com'))
    found = await repo.search({'query_text': 'Fleetwood Mac', 'artists': ['Fleetwood Mac']}, ctx)
    shown = filter_available(found, profile=ctx.profile, owned_record_ids=ctx.owned_record_ids, owned_keys=ctx.owned_keys)
    assert {c['title'] for c in found} == {'Rumours', 'Fleetwood Mac'}      # Tusk is VG, below this customer's VG+
    assert [c['title'] for c in shown] == ['Fleetwood Mac']                   # Rumours is owned
    assert isinstance(found[0]['price'], str)


async def test_everything_returned_is_json_safe(repo):
    """The graph state is checkpointed: no Decimal, datetime or ORM objects may leak into it."""
    uid = await user_id('beat.digger@example.com')
    ctx = await repo.load_context(uid)
    found = await repo.search({'query_text': 'electronic'}, ctx)
    values = [ctx.profile, ctx.collection, found, await repo.get_items([c['item_id'] for c in found[:3]]),
              await repo.record_details(titles=['Blue Train']), await repo.collection_stats(uid),
              await repo.add_wantlist(uid, 'Can', 'Tago Mago')]
    json.dumps(values)   # raises TypeError on anything not JSON-serializable


async def test_get_items_shows_current_status(repo):
    details = await repo.record_details(titles=['Blue Train'])
    item_id = next(c['id'] for d in details for c in d['copies_in_stock'])
    uid = await user_id('beat.digger@example.com')
    await repo.reserve(uid, item_id)
    assert (await repo.get_items([item_id]))[item_id]['status'] == 'reserved'


async def test_reserve_uses_the_atomic_service(repo):
    uid = await user_id('jazz.fan@example.com')                   # already holds 1 reservation (seed)
    copies = [c['id'] for d in await repo.record_details(artists=['Fleetwood Mac']) for c in d['copies_in_stock']]
    first = await repo.reserve(uid, copies[0])
    assert first['inventory_item_id'] == copies[0] and first['expires_at']
    with pytest.raises(ReservationConflict):
        await repo.reserve(await user_id('beat.digger@example.com'), copies[0])
    assert await repo.active_reservation_count(uid) == 2
    await repo.reserve(uid, copies[1])
    with pytest.raises(ReservationLimitReached):
        await repo.reserve(uid, copies[2])


async def test_add_wantlist_dedupes_and_matches(repo):
    uid = await user_id('beat.digger@example.com')
    entry = await repo.add_wantlist(uid, 'Joy Division', 'Unknown Pleasures', max_price='30')
    assert entry['created'] and entry['matched_item_id'] is not None        # the $27 reissue is in stock
    again = await repo.add_wantlist(uid, 'joy division', 'UNKNOWN PLEASURES')
    assert not again['created'] and again['wantlist_id'] == entry['wantlist_id']
    missing = await repo.add_wantlist(uid, 'Nirvana', 'Nevermind')          # already on the demo wantlist
    assert not missing['created'] and missing['matched_item_id'] is None    # only copy is sold


async def test_record_details_for_record_info(repo):
    details = await repo.record_details(titles=['blue train'])
    assert [(d['release_year'], d['pressing']) for d in details] == [(1957, 'original'), (2022, 'reissue')]
    reissue = details[1]
    assert [c['price'] for c in reissue['copies_in_stock']] == ['38.00']    # the other copy is reserved
    assert 'Lazy Bird' in details[0]['tracklist']
    assert await repo.record_details() == []


async def test_knowledge_threshold(repo):
    await seed_knowledge()
    chunk = (await get_knowledge_store().asimilarity_search('x', k=1))[0]
    hits = await repo.search_knowledge(chunk.page_content)                  # identical text → score 1.0
    assert hits[0]['text'] == chunk.page_content and hits[0]['score'] > 0.99
    assert all(h['score'] >= 0.6 for h in hits)
    assert await repo.search_knowledge('What is the capital of France?', min_score=0.99) == []


async def test_collection_stats_and_log_search(repo):
    uid = await user_id('jazz.fan@example.com')
    stats = await repo.collection_stats(uid)
    assert stats['total'] == 4 and stats['by_decade'] == {'1950s': 1, '1970s': 2}
    assert 'Bill Evans Trio — Sunday at the Village Vanguard' in stats['records']

    await repo.log_search(uid, 'find_record', {'genres': ['polka']}, 0, 2)
    async with SessionLocal() as db:
        log = await db.scalar(select(SearchLog))
    assert (log.intent, log.constraints, log.results_count, log.relax_attempts) == ('find_record', {'genres': ['polka']}, 0, 2)
