from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.availability import filter_available

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


def copy(item_id, **overrides):
    """A copy that passes every rule; each test changes only what it is about."""
    item = {
        'item_id': item_id, 'record_id': item_id * 10,
        'artist_norm': f'artist {item_id}', 'title_norm': f'title {item_id}',
        'status': 'in_stock', 'price': Decimal('30.00'), 'media_condition': 'VG+',
        'genres': ['jazz'], 'styles': [],
    }
    return item | overrides


def run(items, profile=None, **kwargs):
    kwargs.setdefault('owned_record_ids', set())
    kwargs.setdefault('owned_keys', set())
    kept = filter_available(items, profile=profile or {}, now=NOW, **kwargs)
    return [i['item_id'] for i in kept]


def test_filter_drops_sold_and_reserved():
    items = [
        copy(1),
        copy(2, status='sold'),
        copy(3, status='reserved', reservation_expires_at=NOW + timedelta(hours=1)),   # still held
        copy(4, status='reserved', reservation_expires_at=NOW - timedelta(minutes=1)),  # expired → released
        copy(5, status='reserved'),                                                      # no expiry known → held
    ]
    assert run(items) == [1, 4]


def test_filter_drops_owned_records():
    items = [copy(1), copy(2)]
    # 1 is owned via the catalog id; 2 via a hand-entered collection entry (names).
    owned = dict(owned_record_ids={10}, owned_keys={('artist 2', 'title 2')})
    assert run(items, **owned) == []
    assert run(items, include_owned=True, **owned) == [1, 2]  # "a better copy than mine"


def test_filter_budget_cap_is_hard():
    items = [copy(1, price=Decimal('39.99')), copy(2, price=Decimal('40.00')), copy(3, price=Decimal('40.01'))]
    # No request max_price involved: the profile cap alone removes copy 3.
    assert run(items, profile={'budget_cap': Decimal('40')}) == [1, 2]
    assert run(items, profile={'budget_cap': '40.00'}) == [1, 2]   # cap as a string (from JSON)


def test_filter_min_condition():
    items = [copy(1, media_condition='VG'), copy(2, media_condition='NM'), copy(3, media_condition='VG+')]
    assert run(items, profile={'min_condition': 'VG+'}) == [2, 3]


def test_disliked_genre_dropped_unless_requested():
    items = [copy(1, genres=['country']), copy(2, genres=['rock'], styles=['Country Rock']), copy(3)]
    profile = {'disliked_genres': ['country', 'country rock']}
    assert run(items, profile=profile) == [3]                                     # genres and styles both count
    assert run(items, profile=profile, requested_genres=['Country']) == [1, 3]   # explicit request wins for "country" only


def test_empty_profile_keeps_everything_in_stock():
    items = [copy(1), copy(2, media_condition='P', price=Decimal('999'))]
    assert run(items, profile={}) == [1, 2]
    assert run(items, profile={'budget_cap': None, 'min_condition': None, 'disliked_genres': []}) == [1, 2]


def test_all_rules_together_and_order_preserved():
    items = [
        copy(5, price=Decimal('20')),
        copy(1, status='sold'),
        copy(3, media_condition='G'),
        copy(2, price=Decimal('80')),
        copy(4),
    ]
    assert run(items, profile={'budget_cap': 50, 'min_condition': 'VG'}) == [5, 4]
