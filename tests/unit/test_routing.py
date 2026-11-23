"""Every routing decision of the graph, as a table. Pure functions, no graph needed."""

import pytest
from langgraph.graph import END

from app.ai.routing import (
    RELAX_EXHAUSTED,
    route_after_context,
    route_after_filter,
    route_after_prepare,
    route_after_relax,
    route_after_understand,
    route_after_verify,
)


@pytest.mark.parametrize(('intent', 'node'), [
    ('find_record', 'load_context'), ('record_info', 'load_context'), ('reserve', 'load_context'),
    ('wantlist', 'load_context'), ('collection_help', 'load_context'),
    ('off_topic', 'polite_refusal'), ('something_new', 'polite_refusal'), (None, 'polite_refusal'),
])
def test_after_understand(intent, node):
    """Unknown intents are refused too: the safe default."""
    assert route_after_understand({'intent': intent}) == node


@pytest.mark.parametrize(('intent', 'node'), [
    ('find_record', 'search_inventory'), ('record_info', 'retrieve_knowledge'),
    ('reserve', 'prepare_reservation'), ('wantlist', 'prepare_wantlist_entry'),
    ('collection_help', 'collection_insights'),
])
def test_after_context(intent, node):
    assert route_after_context({'intent': intent}) == node


@pytest.mark.parametrize(('available', 'attempts', 'node'), [
    ([{'item_id': 1}], 0, 'compose_answer'),
    ([{'item_id': 1}], 2, 'compose_answer'),          # found after relaxing
    ([], 0, 'relax_constraints'),
    ([], 1, 'relax_constraints'),
    ([], 2, 'offer_wantlist'),                       # at most 2 relaxations
])
def test_after_filter(available, attempts, node):
    assert route_after_filter({'available_items': available, 'relax_attempts': attempts}) == node


@pytest.mark.parametrize(('attempts', 'node'), [(1, 'search_inventory'), (2, 'search_inventory'),
                                                 (RELAX_EXHAUSTED, 'offer_wantlist')])
def test_after_relax(attempts, node):
    assert route_after_relax({'relax_attempts': attempts}) == node


@pytest.mark.parametrize(('pending', 'node'), [({'type': 'reserve'}, 'confirm_action'), (None, 'compose_answer')])
def test_after_prepare(pending, node):
    assert route_after_prepare({'pending_action': pending}) == node


@pytest.mark.parametrize(('issues', 'attempts', 'node'), [
    ([], 1, END), ([], 2, END),
    (['bad price'], 1, 'compose_answer'),            # one retry with the issues
    (['bad price'], 2, 'fallback_template'),         # two failures → template
])
def test_after_verify(issues, attempts, node):
    assert route_after_verify({'answer_issues': issues, 'answer_attempts': attempts}) == node
