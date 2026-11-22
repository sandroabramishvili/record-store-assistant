"""The seed data must satisfy the spec (8.1) and the API's own validation.

Runs without a database: it only reads the JSON files and the Pydantic schemas.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from app.schemas.catalog import InventoryIn, RecordIn
from app.schemas.collection import CollectionCreate, WantlistCreate
from app.schemas.user import ProfileUpdate, UserCreate
from app.services.normalize import normalize_name

DATA = Path(__file__).resolve().parents[2] / 'scripts' / 'data'
RECORDS = json.loads((DATA / 'records.json').read_text(encoding='utf-8'))
USERS = json.loads((DATA / 'demo_users.json').read_text(encoding='utf-8'))
COPIES = [c for r in RECORDS for c in r['copies']]


def key(r):
    return normalize_name(r['artist']), normalize_name(r['title'])


# ---------- validity ----------

@pytest.mark.parametrize('record', RECORDS, ids=lambda r: f"{r['artist']} - {r['title']} ({r['release_year']})")
def test_record_passes_api_validation(record):
    RecordIn.model_validate({k: v for k, v in record.items() if k != 'copies'})
    for copy in record['copies']:
        InventoryIn.model_validate({k: v for k, v in copy.items() if k not in ('status', 'reserved_by')})
        assert copy['status'] in ('in_stock', 'reserved', 'sold')


def test_no_duplicate_pressings():
    pressings = Counter((*key(r), r['release_year'], r['label']) for r in RECORDS)
    assert [p for p, n in pressings.items() if n > 1] == []


def test_reserved_copies_have_a_valid_holder():
    customers = {u['email'] for u in USERS if u['role'] == 'customer'}
    holders = Counter(c['reserved_by'] for c in COPIES if c['status'] == 'reserved')
    assert set(holders) <= customers
    assert all(n < 3 for n in holders.values())   # leave room under the 3-reservation limit for the demo
    assert all('reserved_by' not in c for c in COPIES if c['status'] != 'reserved')


def test_demo_users_pass_api_validation():
    roles = Counter(u['role'] for u in USERS)
    assert roles == {'staff': 1, 'customer': 2}
    for u in USERS:
        UserCreate.model_validate(u)
        ProfileUpdate.model_validate(u['profile'])
        for item in u['collection']:
            CollectionCreate.model_validate(item)
        for item in u['wantlist']:
            WantlistCreate.model_validate(item)


# ---------- spec 8.1 ----------

def test_minimum_sizes():
    assert len(RECORDS) >= 40
    assert len(COPIES) >= 60


def test_at_least_six_genres():
    genres = {g for r in RECORDS for g in r['genres']}
    assert len(genres) >= 6, genres


def test_decades_from_1950s_to_today():
    decades = {r['original_year'] // 10 * 10 for r in RECORDS}
    assert {1950, 1960, 1970, 1980, 1990, 2000, 2010} <= decades | {r['release_year'] // 10 * 10 for r in RECORDS}
    assert min(decades) <= 1950 and max(r['release_year'] for r in RECORDS) >= 2015


def test_at_least_three_albums_as_original_and_reissue():
    originals = {key(r) for r in RECORDS if r['release_year'] == r['original_year']}
    reissues = {key(r) for r in RECORDS if r['release_year'] != r['original_year']}
    assert len(originals & reissues) >= 3


def test_sold_and_reserved_copies_exist():
    statuses = Counter(c['status'] for c in COPIES)
    assert statuses['sold'] >= 3 and statuses['reserved'] >= 2


def test_some_releases_have_several_copies_in_different_grades():
    multi = [r for r in RECORDS if len({c['media_condition'] for c in r['copies']}) >= 2]
    assert len(multi) >= 5


# ---------- the demo scenarios (spec section 12) have data ----------

def by(artist, title=None):
    return [r for r in RECORDS if normalize_name(r['artist']) == normalize_name(artist)
            and (title is None or normalize_name(r['title']) == normalize_name(title))]


def test_demo_70s_jazz_funk_under_40():
    """Original 70s jazz-funk, in stock, ≤ $40, ≥ VG+ exists; a reissue and a VG copy are there to be filtered."""
    from app.services.conditions import meets_minimum
    hits = [r for r in RECORDS if 'jazz-funk' in r['styles'] and 1970 <= r['original_year'] <= 1979
            and r['release_year'] == r['original_year']
            and any(c['status'] == 'in_stock' and float(c['price']) <= 40 and meets_minimum(c['media_condition'], 'VG+')
                    for c in r['copies'])]
    assert len(hits) >= 2


def test_demo_nevermind_not_in_stock():
    assert by('Nirvana', 'Nevermind')
    assert all(c['status'] != 'in_stock' for r in by('Nirvana', 'Nevermind') for c in r['copies'])


def test_demo_fleetwood_mac_and_radiohead():
    assert len({r['title'] for r in by('Fleetwood Mac')}) >= 3
    assert len({r['title'] for r in by('Radiohead')}) >= 4
    owned = {key(i) for u in USERS for i in u['collection']}
    assert key({'artist': 'Fleetwood Mac', 'title': 'Rumours'}) in owned


def test_demo_blue_train_original_and_reissue():
    years = {r['release_year'] for r in by('John Coltrane', 'Blue Train')}
    assert 1957 in years and len(years) >= 2
