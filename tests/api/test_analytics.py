"""Staff analytics (Phase 15, spec 7.9): exact numbers from known rows, and staff-only access."""

from datetime import timedelta

import pytest
from sqlalchemy import func, update

from app.db import SessionLocal
from app.models import ChatMessage, ChatSession, Reservation, SearchLog, WantlistItem
from app.services.normalize import normalize_name
from app.services.reservations import cancel_reservation, complete_reservation, reserve_item

ENDPOINTS = ['/analytics/demand', '/analytics/unmet-demand', '/analytics/reservations', '/analytics/intents']


async def add_logs(user_id: int, *rows: tuple) -> None:
    """rows: (constraints, results_count) or (constraints, results_count, intent, days_ago)."""
    async with SessionLocal() as db:
        for row in rows:
            constraints, results, intent, days_ago = (*row, 'find_record', 0)[:4]   # defaults for 2-tuples
            db.add(SearchLog(user_id=user_id, intent=intent, constraints=constraints, results_count=results,
                             relax_attempts=0 if results else 2, created_at=func.now() - timedelta(days=days_ago)))
        await db.commit()


@pytest.mark.parametrize('path', ENDPOINTS)
async def test_staff_only(client, customer, path):
    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, headers=customer.headers)).status_code == 403


async def test_empty_database_gives_zeros_not_errors(client, staff):
    for path in ENDPOINTS:
        r = await client.get(path, headers=staff.headers)
        assert r.status_code == 200, (path, r.text)
    stats = (await client.get('/analytics/reservations', headers=staff.headers)).json()
    assert stats['created'] == 0 and stats['conversion_rate'] is None          # no division by zero


async def test_demand_counts_what_customers_asked_for(client, staff, customer):
    await add_logs(
        customer.id,
        ({'genres': ['jazz', 'funk'], 'artists': ['Radiohead'], 'decade': 1970}, 3),
        ({'genres': ['jazz'], 'artists': ['radiohead '], 'decade': 1970}, 1),     # same artist, other spelling
        ({'genres': ['Jazz', 'soul'], 'artists': [], 'titles': ['Blue Train'], 'decade': None}, 0),
        ({'genres': None, 'artists': ['Miles Davis']}, 2),                       # null list: skipped, no error
        ({'genres': ['polka']}, 0, 'find_record', 40),                          # outside the 30-day window
        ({'genres': ['grading']}, 0, 'record_info', 0),                          # not a search
    )
    body = (await client.get('/analytics/demand', headers=staff.headers)).json()
    assert body['searches'] == 4
    assert body['genres'] == [{'name': 'jazz', 'count': 3}, {'name': 'funk', 'count': 1}, {'name': 'soul', 'count': 1}]
    assert body['artists'] == [{'name': 'Radiohead', 'count': 2}, {'name': 'Miles Davis', 'count': 1}]
    assert body['titles'] == [{'name': 'Blue Train', 'count': 1}]
    assert body['decades'] == [{'name': '1970s', 'count': 2}]

    longer = (await client.get('/analytics/demand?days=60&limit=1', headers=staff.headers)).json()
    assert longer['searches'] == 5 and longer['genres'] == [{'name': 'jazz', 'count': 3}]


async def test_unmet_demand_zero_results_and_most_wanted(client, staff, customer, other_customer, add_copy):
    await add_logs(
        customer.id,
        ({'titles': ['Nevermind'], 'artists': ['Nirvana'], 'max_price': 30, 'query_text': 'Nevermind',
          'original_pressing_only': True, 'similar_to': None}, 0),
        ({'titles': ['Nevermind'], 'genres': ['grunge']}, 0),
        ({'genres': ['jazz']}, 4),
    )
    copy_id = await add_copy()
    async with SessionLocal() as db:
        def want(user_id, artist, title=None, max_price=None, matched=None):
            db.add(WantlistItem(user_id=user_id, artist=artist, title=title, artist_norm=normalize_name(artist),
                                title_norm=normalize_name(title) if title else None, max_price=max_price,
                                matched_item_id=matched))
        want(customer.id, 'Nirvana', 'Nevermind', 25)
        want(other_customer.id, 'nirvana', 'NEVERMIND', 40)                     # same record, normalized
        want(other_customer.id, 'Björk')                                         # anything by Björk
        want(customer.id, 'Herbie Hancock', 'Head Hunters', matched=copy_id)     # already matched: not unmet
        await db.commit()

    body = (await client.get('/analytics/unmet-demand', headers=staff.headers)).json()
    assert (body['searches'], body['zero_result_searches'], body['zero_result_rate']) == (3, 2, 0.667)
    assert body['titles'] == [{'name': 'Nevermind', 'count': 2}]
    assert body['genres'] == [{'name': 'grunge', 'count': 1}]
    assert body['recent'][1]['asked'] == {'titles': ['Nevermind'], 'artists': ['Nirvana'], 'max_price': 30,
                                          'query_text': 'Nevermind', 'original_pressing_only': True}
    assert body['recent'][0]['relax_attempts'] == 2
    wanted = [(w['artist'], w['title'], w['customers'], w['highest_max_price']) for w in body['most_wanted']]
    assert wanted == [('Nirvana', 'Nevermind', 2, '40.00'), ('Björk', None, 1, None)]   # first-written spelling


async def test_reservation_stats_and_conversion(client, staff, make_customers, add_copy):
    buyers = await make_customers(4)
    copies = [await add_copy() for _ in range(4)]
    async with SessionLocal() as db:
        made = [await reserve_item(db, b.id, c) for b, c in zip(buyers, copies)]
        ids = [r.id for r in made]
        await complete_reservation(db, ids[0])
        await cancel_reservation(db, ids[1])
        # ids[2]: its 48 h are over, but nobody has looked yet (lazy expiry)
        await db.execute(update(Reservation).where(Reservation.id == ids[2])
                         .values(expires_at=func.now() - timedelta(minutes=1)))
        await db.commit()

    body = (await client.get('/analytics/reservations', headers=staff.headers)).json()
    assert {k: body[k] for k in ('created', 'active', 'completed', 'cancelled', 'expired')} == \
        {'created': 4, 'active': 1, 'completed': 1, 'cancelled': 1, 'expired': 1}   # expiry applied first
    assert body['conversion_rate'] == 0.25 and body['completion_rate_of_closed'] == 0.333


async def test_intents_distribution(client, staff, customer):
    async with SessionLocal() as db:
        chat = ChatSession(user_id=customer.id)
        db.add(chat)
        await db.flush()
        for intent in ['find_record'] * 3 + ['off_topic', 'record_info', None]:   # None: user rows / resumed turns
            db.add(ChatMessage(session_id=chat.id, role='assistant', content='…', intent=intent))
        db.add(ChatMessage(session_id=chat.id, role='assistant', content='old', intent='reserve',
                           created_at=func.now() - timedelta(days=90)))
        await db.commit()

    body = (await client.get('/analytics/intents', headers=staff.headers)).json()
    assert body['total'] == 5
    assert body['intents'] == [{'name': 'find_record', 'count': 3, 'share': 0.6},
                               {'name': 'off_topic', 'count': 1, 'share': 0.2},
                               {'name': 'record_info', 'count': 1, 'share': 0.2}]
