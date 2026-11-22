import copy

import pytest

from app.services.relax import MAX_RELAX_ATTEMPTS, relax_constraints

HARD_FIELDS = ['min_condition', 'include_owned', 'format', 'artists', 'original_pressing_only']


def request(**overrides):
    base = {
        'artists': [], 'genres': ['jazz-funk'], 'similar_to': None, 'decade': 1970,
        'max_price': 40.0, 'min_condition': 'VG+', 'format': 'LP',
        'original_pressing_only': True, 'include_owned': False, 'query_text': '70s jazz-funk',
    }
    return base | overrides


def test_attempt_1_raises_price_by_25_percent():
    out = relax_constraints(request(), 1, profile={})
    assert out['max_price'] == 50.0
    assert out['decade'] == 1970 and out['genres'] == ['jazz-funk']   # nothing else yet


def test_price_never_above_budget_cap():
    assert relax_constraints(request(), 1, profile={'budget_cap': '45'})['max_price'] == 45.0
    # already at the cap → nothing to gain from price, go straight to decade/genres
    out = relax_constraints(request(max_price=45.0), 1, profile={'budget_cap': 45})
    assert out['max_price'] == 45.0 and out['decade'] is None


def test_attempt_2_drops_decade_and_widens_genres():
    out = relax_constraints(request(), 2, profile={})
    assert out['decade'] is None
    assert out['genres'] == ['jazz-funk', 'jazz', 'funk', 'soul']


def test_no_price_goes_straight_to_decade_and_genres():
    out = relax_constraints(request(max_price=None), 1, profile={})
    assert out['decade'] is None and 'jazz' in out['genres']


def test_relax_never_touches_hard_rules():
    """Spec: stock, ownership, budget_cap and min_condition are never relaxed."""
    profile = {'budget_cap': 45, 'min_condition': 'NM', 'disliked_genres': ['country']}
    profile_before = copy.deepcopy(profile)
    c = request()
    for attempt in range(1, MAX_RELAX_ATTEMPTS + 1):
        new = relax_constraints(c, attempt, profile)
        for field in HARD_FIELDS:
            assert new[field] == c[field]
        assert new['max_price'] <= 45                     # cap respected
        assert 'status' not in new and 'budget_cap' not in new
        c = new
    assert profile == profile_before                      # profile untouched


def test_input_is_not_mutated():
    original = request()
    snapshot = copy.deepcopy(original)
    relax_constraints(original, 1, profile={})
    relax_constraints(original, 2, profile={})
    assert original == snapshot


def test_relax_loop_limit():
    """At most 2 relaxations; after that the graph offers the wantlist instead."""
    with pytest.raises(ValueError):
        relax_constraints(request(), MAX_RELAX_ATTEMPTS + 1, profile={})
    # Nothing left to loosen → result equals input, the signal to stop early.
    bare = request(max_price=None, decade=None, genres=['polka'])
    assert relax_constraints(bare, 1, profile={}) == bare
