"""Hybrid search on the full seeded catalog (fake embeddings: the filters are what's tested)."""

import itertools
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import update

from app.ai.vectorstores import get_record_store
from app.db import SessionLocal
from app.models import Reservation
from app.repositories.search import search_inventory
from app.services.conditions import meets_minimum
from scripts.seed_catalog import seed_catalog


@pytest_asyncio.fixture
async def catalog():
    await seed_catalog()


async def search(constraints=None, profile=None, **kwargs):
    async with SessionLocal() as db:
        return await search_inventory(db, get_record_store(), {'query_text': 'records'} | (constraints or {}),
                                      profile or {}, **kwargs)


async def test_strict_filters_are_never_violated(catalog):
    """Every combination of price / condition / decade / format / original-only."""
    prices, grades, decades = [None, 25, 40, 100], [None, 'VG', 'VG+', 'NM'], [None, 1950, 1970, 1990]
    formats, originals = [None, 'LP', '7"'], [False, True]
    checked = 0
    for price, grade, decade, fmt, orig in itertools.product(prices, grades, decades, formats, originals):
        c = {'max_price': price, 'min_condition': grade, 'decade': decade, 'format': fmt, 'original_pressing_only': orig}
        for r in await search(c, limit=100):
            assert r['status'] == 'in_stock'
            assert price is None or r['price'] <= price
            assert meets_minimum(r['media_condition'], grade)
            assert decade is None or decade <= r['original_year'] <= decade + 9
            assert fmt is None or r['format'] == fmt
            assert not (orig and r['is_reissue'])
            checked += 1
    assert checked > 500


async def test_profile_budget_and_condition_are_hard(catalog):
    profile = {'budget_cap': Decimal('30'), 'min_condition': 'NM'}
    results = await search({'max_price': 500}, profile, limit=100)    # request asks for more
    assert results and all(r['price'] <= 30 and meets_minimum(r['media_condition'], 'NM') for r in results)


async def test_demo_70s_jazz_funk(catalog):
    results = await search({'query_text': '70s jazz-funk', 'decade': 1970, 'max_price': 40, 'min_condition': 'VG+',
                            'original_pressing_only': True, 'genres': ['jazz-funk']})
    titles = {r['title'] for r in results}
    assert titles == {'Head Hunters', 'Black Byrd', 'Everybody Loves the Sunshine'}
    assert all(not r['is_reissue'] for r in results)


async def test_genres_match_styles_too_and_artists_are_normalized(catalog):
    by_style = await search({'genres': ['Shoegaze']})                       # a style, in another case
    assert {r['title'] for r in by_style} == {'Loveless'}
    by_artist = await search({'artists': ['the beatles ']}, limit=100)
    assert by_artist and {r['artist'] for r in by_artist} == {'The Beatles'}
    by_title = await search({'titles': ['ABBEY ROAD']}, limit=100)
    assert by_title and {r['title'] for r in by_title} == {'Abbey Road'}
    assert await search({'titles': ['Nevermind']}) == []     # in the catalog, but its only copy is sold


async def test_similar_to_excludes_the_reference_release(catalog):
    results = await search({'query_text': 'more upbeat', 'similar_to': "Miles Davis' Kind of Blue"}, limit=100)
    assert results and all(r['title'] != 'Kind of Blue' for r in results)


async def test_sold_and_reserved_never_returned_but_expired_hold_is(catalog):
    assert await search({'artists': ['Nirvana']}) == []        # Nevermind's only copy is sold

    async def blue_train_reissue_copies():
        found = await search({'artists': ['John Coltrane']}, limit=100)
        return [r for r in found if r['title'] == 'Blue Train' and r['release_year'] == 2022]

    # The 2022 reissue has two copies: one in stock, one reserved by the jazz fan.
    assert len(await blue_train_reissue_copies()) == 1
    async with SessionLocal() as db:                           # pretend the 48 hours are over
        await db.execute(update(Reservation).values(expires_at=Reservation.created_at))
        await db.commit()
    assert len(await blue_train_reissue_copies()) == 2         # the held copy is on sale again


async def test_results_are_ranked_and_taste_counts(catalog):
    profile = {'favorite_genres': ['electronic', 'idm']}
    results = await search({}, profile, collection_artists={'kraftwerk'}, limit=60)
    scores = [r['score'] for r in results]
    assert scores == sorted(scores, reverse=True)
    kraftwerk = [r for r in results if r['artist'] == 'Kraftwerk']
    assert kraftwerk and all(r['taste'] >= 0.3 for r in kraftwerk)


async def test_nothing_matches_returns_empty(catalog):
    assert await search({'genres': ['polka']}) == []
    assert await search({'max_price': 1}) == []


@pytest.mark.parametrize('limit', [1, 5])
async def test_limit(catalog, limit):
    assert len(await search({}, limit=limit)) == limit
