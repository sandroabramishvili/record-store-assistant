from decimal import Decimal

import pytest

from app.services.normalize import normalize_name
from app.services.wantlist import best_copy, entry_matches, find_matches


def entry(id=1, artist='Kraftwerk', title=None, max_price=None, min_condition=None):
    return {'id': id, 'artist_norm': normalize_name(artist), 'title_norm': normalize_name(title) if title else None,
            'max_price': max_price, 'min_condition': min_condition, 'matched_item_id': None}


def copy(id=10, artist='Kraftwerk', title='Autobahn', price='30.00', grade='VG+', status='in_stock'):
    return {'id': id, 'artist_norm': normalize_name(artist), 'title_norm': normalize_name(title),
            'price': Decimal(price), 'media_condition': grade, 'status': status}


@pytest.mark.parametrize(
    ('e', 'c', 'ok'),
    [
        (entry(), copy(), True),                                         # anything by the artist
        (entry(title='Autobahn'), copy(), True),
        (entry(title='Computer World'), copy(), False),                  # other album
        (entry(artist='Neu!'), copy(), False),                           # other artist
        (entry(max_price=Decimal('30')), copy(price='30.00'), True),     # price limit inclusive
        (entry(max_price=Decimal('29.99')), copy(price='30.00'), False),
        (entry(min_condition='VG+'), copy(grade='NM'), True),
        (entry(min_condition='VG+'), copy(grade='VG'), False),
        (entry(), copy(status='reserved'), False),                      # only in-stock copies
        (entry(), copy(status='sold'), False),
    ],
)
def test_entry_matches(e, c, ok):
    assert entry_matches(e, c) is ok


def test_matching_uses_normalized_names():
    # Wantlist typed as "kraftwerk / AUTOBAHN", catalog says "Kraftwerk – Autobahn".
    assert entry_matches(entry(artist='kraftwerk ', title='AUTOBAHN'), copy())
    assert entry_matches(entry(artist='Motorhead', title='Ace of Spades'),
                         copy(artist='Motörhead', title='Ace Of Spades'))


def test_find_matches_returns_all_fitting_entries():
    entries = [entry(1), entry(2, title='Autobahn', max_price=Decimal('25')), entry(3, title='Autobahn'),
               entry(4, artist='Can')]
    assert find_matches(copy(price='30.00'), entries) == [1, 3]


def test_best_copy_cheapest_then_best_condition():
    copies = [
        copy(1, price='40.00', grade='M'),
        copy(2, price='30.00', grade='VG'),
        copy(3, price='30.00', grade='NM'),       # same price as 2, better condition → wins
        copy(4, price='20.00', status='sold'),    # cheapest but not in stock
        copy(5, price='10.00', title='Radio-Activity'),
    ]
    assert best_copy(entry(title='Autobahn'), copies) == 3
    assert best_copy(entry(), copies) == 5                    # any title → the cheapest overall
    assert best_copy(entry(title='Autobahn', min_condition='M'), copies) == 1
    assert best_copy(entry(title='Autobahn', max_price=Decimal('15')), copies) is None
