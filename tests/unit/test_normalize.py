import pytest

from app.services.availability import filter_available
from app.services.normalize import normalize_name


@pytest.mark.parametrize(
    ('a', 'b'),
    [
        ('Motörhead', 'Motorhead'),
        ('Simon & Garfunkel', 'Simon and Garfunkel'),
        ("Sgt. Pepper's Lonely Hearts Club Band", 'Sgt Peppers Lonely Hearts Club Band'),
        ('Sgt. Pepper’s Lonely Hearts Club Band', "Sgt. Pepper's Lonely Hearts Club Band"),  # curly apostrophe
        ('R.E.M.', 'REM'),
        ('Trans-Europe Express', 'Trans Europe Express'),
        ('  Miles   Davis ', 'miles davis'),
        ('Ｂｊöｒｋ', 'Bjork'),  # full-width letters
    ],
)
def test_same_key(a, b):
    assert normalize_name(a) == normalize_name(b)


def test_name_normalization():
    """Same artist: "The Beatles", "beatles " and "BEATLES" match the same collection entry."""
    variants = ['The Beatles', 'beatles ', 'BEATLES', 'Beatles, The']
    assert {normalize_name(v) for v in variants} == {'beatles'}

    # ...and therefore the owned-record rule catches every spelling.
    owned = {(normalize_name('beatles '), normalize_name('ABBEY ROAD'))}
    copy = {'item_id': 1, 'record_id': 9, 'artist_norm': normalize_name('The Beatles'),
            'title_norm': normalize_name('Abbey Road'), 'status': 'in_stock', 'price': 30,
            'media_condition': 'VG+', 'genres': ['rock']}
    assert filter_available([copy], profile={}, owned_record_ids=set(), owned_keys=owned) == []


def test_the_only_removed_as_a_prefix():
    assert normalize_name('The The') == 'the'           # the band "The The"
    assert normalize_name('The') == 'the'
    assert normalize_name('Theatre of Hate') == 'theatre of hate'
    assert normalize_name('Enter the Dragon') == 'enter the dragon'


def test_non_latin_scripts_are_kept():
    assert normalize_name('ქართული ხმები') == 'ქართული ხმები'   # Georgian unchanged
    assert normalize_name('ガガガSP') == 'ガガガsp'               # Japanese voicing marks kept


def test_different_names_stay_different():
    assert normalize_name('Beatles') != normalize_name('Beat')
    assert normalize_name('Blue Train') != normalize_name('Blue Trane')
