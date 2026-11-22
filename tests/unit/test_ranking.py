from decimal import Decimal

import pytest

from app.services.ranking import final_score, rank, rescale


def test_rescale_spreads_a_narrow_band():
    """Gemini similarities cluster (e.g. 0.60-0.72); after rescaling best = 1, worst = 0."""
    out = rescale({1: 0.72, 2: 0.66, 3: 0.60})
    assert out[1] == 1.0 and out[3] == 0.0 and out[2] == pytest.approx(0.5)


def test_rescale_nearly_equal_means_no_order():
    assert rescale({1: 0.700, 2: 0.705, 3: 0.71}) == {1: 1.0, 2: 1.0, 3: 1.0}
    assert rescale({}) == {}


def test_final_score_weights():
    assert final_score(1.0, 0.0) == 0.6
    assert final_score(0.0, 1.0) == 0.4
    assert final_score(1.0, 1.0) == 1.0


def copy(item_id, record_id, price='30'):
    return {'item_id': item_id, 'record_id': record_id, 'price': Decimal(price)}


def test_rank_request_leads_taste_nudges():
    sim = {1: 0.72, 2: 0.60}           # release 1 fits the request best
    taste = {1: 0.0, 2: 1.0}           # release 2 fits the profile perfectly
    ranked = rank([copy(10, 2), copy(11, 1)], sim, taste)
    assert [c['record_id'] for c in ranked] == [1, 2]      # 0.6 beats 0.4: the request leads

    close = {1: 0.705, 2: 0.70}        # nearly equal relevance → taste decides
    assert [c['record_id'] for c in rank([copy(10, 2), copy(11, 1)], close, taste)] == [2, 1]


def test_rank_ties_cheaper_copy_first_and_scores_attached():
    ranked = rank([copy(1, 5, '50'), copy(2, 5, '20')], {5: 0.7}, {5: 0.5})
    assert [c['item_id'] for c in ranked] == [2, 1]
    assert set(ranked[0]) >= {'similarity', 'taste', 'score'}


def test_release_missing_from_vector_search_gets_zero_similarity():
    ranked = rank([copy(3, 7), copy(1, 5)], {5: 0.7}, {})
    assert [c['record_id'] for c in ranked] == [5, 7]
    assert ranked[-1]['similarity'] == 0.0 and ranked[-1]['score'] == 0.0
