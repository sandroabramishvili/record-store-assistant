"""Each node on its own, with FakeLLM + FakeRepo (no Gemini, no database)."""

from langchain_core.messages import AIMessage, HumanMessage

from app.ai.deps import Deps
from app.ai.nodes import RELAX_EXHAUSTED, Nodes, card_label
from app.ai.prompts import REFUSAL_MESSAGE
from app.ai.schemas import AnswerDraft, RequestAnalysis, SearchConstraints
from app.ai.state import new_turn_input
from app.services.customer import CustomerContext
from tests.graph.fakes import FakeLLM, FakeRepo, copy_row

HEAD_HUNTERS = copy_row(57, 5, 'Herbie Hancock', 'Head Hunters', price='38.00')
BLACK_BYRD = copy_row(58, 6, 'Donald Byrd', 'Black Byrd', price='32.00')
RUMOURS = copy_row(60, 8, 'Fleetwood Mac', 'Rumours', price='30.00', genres=('rock',))
TUSK = copy_row(61, 9, 'Fleetwood Mac', 'Tusk', price='80.00', genres=('rock',))
JAZZ_FAN = CustomerContext(profile={'budget_cap': '60.00', 'min_condition': 'VG+', 'disliked_genres': ['hip-hop']},
                           collection=[{'record_id': 8, 'artist_norm': 'fleetwood mac', 'title_norm': 'rumours'}])


def make(*responses, items=(HEAD_HUNTERS, BLACK_BYRD, RUMOURS, TUSK), ctx=JAZZ_FAN):
    llm, repo = FakeLLM(*responses), FakeRepo(items, ctx)
    return Nodes(Deps(llm=llm, repo=repo)), llm, repo


def turn(text='hi', **state):
    """A state as it looks after understand_request + load_context."""
    base = new_turn_input(text, user_id=1) | {'intent': 'find_record'}
    base['request_constraints'] = state.pop('asked', {'query_text': text})
    base['constraints'] = state.pop('used', dict(base['request_constraints']))
    ctx = state.pop('ctx', JAZZ_FAN)
    return base | {'profile': ctx.profile, 'collection': ctx.collection, 'wantlist_artists': []} | state


def analysis(intent='find_record', ref=None, **constraints):
    return RequestAnalysis(intent=intent, constraints=SearchConstraints(query_text='x', **constraints),
                           refers_to_previous=ref is not None, reference_index=ref)


# ---------- understanding ----------

async def test_understand_passes_numbered_last_shown_list_and_keeps_original_constraints():
    nodes, llm, repo = make(analysis('reserve', ref=2))
    repo.items[58]['status'] = 'sold'
    state = turn('the second one please', shown_item_ids=[57, 58],
                 messages=[HumanMessage('jazz-funk?'), AIMessage('1. ...'), HumanMessage('the second one please')])
    out = await nodes.understand_request(state)
    assert out['intent'] == 'reserve' and out['reference_index'] == 2
    assert out['request_constraints'] == out['constraints'] and out['request_constraints'] is not out['constraints']
    prompt = llm.prompts('RequestAnalysis')[0]
    assert '1. Herbie Hancock — Head Hunters (1973), VG+/VG, $38.00' in prompt
    assert '2. Donald Byrd — Black Byrd (1973), VG+/VG, $32.00 (no longer available)' in prompt
    assert 'customer: jazz-funk?' in prompt


async def test_refusal_needs_no_llm():
    nodes, llm, _ = make()
    out = await nodes.polite_refusal(turn())
    assert out['answer'] == REFUSAL_MESSAGE and llm.calls == []


# ---------- searching ----------

async def test_filter_applies_store_rules_and_logs_final_result():
    nodes, _, repo = make()
    state = turn('fleetwood mac', candidates=[RUMOURS, TUSK, HEAD_HUNTERS])
    out = await nodes.availability_filter(state)
    assert [i['item_id'] for i in out['available_items']] == [57]   # Rumours owned, Tusk over the $60 cap
    assert repo.logs == [('find_record', 1, 0)]


async def test_empty_filter_is_logged_only_when_relaxing_is_over():
    nodes, _, repo = make()
    await nodes.availability_filter(turn(candidates=[]))
    assert repo.logs == []                                             # will relax first
    await nodes.availability_filter(turn(candidates=[], relax_attempts=2))
    assert repo.logs == [('find_record', 0, 2)]


async def test_relax_raises_price_but_not_above_budget_and_marks_exhaustion():
    nodes, _, repo = make()
    out = await nodes.relax_constraints(turn(used={'query_text': 'x', 'max_price': 40.0}))
    assert out == {'constraints': {'query_text': 'x', 'max_price': 50.0}, 'relax_attempts': 1}
    out = await nodes.relax_constraints(turn(used={'query_text': 'x', 'max_price': 55.0}))
    assert out['constraints']['max_price'] == 60.0                     # capped by the profile budget
    out = await nodes.relax_constraints(turn(used={'query_text': 'x', 'genres': ['polka']}))
    assert out['constraints'] == {'query_text': 'x polka', 'genres': []}  # unknown genre: drop the filter
    out = await nodes.relax_constraints(turn(used={'query_text': 'x'}))
    assert out == {'relax_attempts': RELAX_EXHAUSTED} and repo.logs == [('find_record', 0, 0)]


async def test_offer_wantlist_needs_an_artist():
    nodes, _, _ = make()
    offer = await nodes.offer_wantlist(turn(asked={'query_text': 'x', 'artists': ['Nirvana'], 'titles': ['Nevermind']}))
    assert offer['pending_action']['type'] == 'wantlist' and 'Nirvana – Nevermind' in offer['pending_action']['question']
    assert (await nodes.offer_wantlist(turn(asked={'query_text': '70s soul'})))['pending_action'] is None


async def test_offer_wantlist_finds_the_artist_of_a_catalog_title():
    """Found in a real run: "Do you have Nevermind?" came back with titles but no artists."""
    nodes, _, repo = make()
    repo.details = [{'record_id': 31, 'artist': 'Nirvana', 'title': 'Nevermind', 'copies_in_stock': []}]
    offer = await nodes.offer_wantlist(turn(asked={'query_text': 'x', 'titles': ['nevermind']}))
    assert offer['pending_action']['artist'] == 'Nirvana' and offer['pending_action']['title'] == 'Nevermind'


# ---------- reservation ----------

async def test_reference_index_resolved_by_code():
    """Reference by position: "the second one" maps to shown_item_ids[1]; out of range reserves nothing."""
    nodes, _, repo = make()
    out = await nodes.prepare_reservation(turn(shown_item_ids=[57, 58], reference_index=2))
    assert out['pending_action']['inventory_item_id'] == 58 and out['pending_action']['price'] == '32.00'
    for bad in (None, 0, 3):
        out = await nodes.prepare_reservation(turn(shown_item_ids=[57, 58], reference_index=bad))
        assert out['pending_action'] is None and out['action_result']['status'] == 'unclear'
    assert repo.reserved == []


async def test_prepare_reservation_rechecks_stock_and_limit():
    nodes, _, repo = make()
    repo.items[57]['status'] = 'reserved'
    out = await nodes.prepare_reservation(turn(shown_item_ids=[57], reference_index=1))
    assert out['action_result']['status'] == 'unavailable'
    repo.active = 3
    out = await nodes.prepare_reservation(turn(shown_item_ids=[58], reference_index=None))  # only one shown → that one
    assert out['action_result']['status'] == 'limit'


async def test_execute_only_after_yes_and_conflicts_become_sentences():
    nodes, _, repo = make()
    pending = (await nodes.prepare_reservation(turn(shown_item_ids=[57], reference_index=1)))['pending_action']

    out = await nodes.execute_action(turn(pending_action=pending, confirmed=False))
    assert out['action_result']['status'] == 'cancelled' and repo.reserved == []

    repo.items[57]['status'] = 'reserved'                              # someone was faster
    out = await nodes.execute_action(turn(pending_action=pending, confirmed=True))
    assert out['action_result']['status'] == 'failed' and 'someone just reserved' in out['action_result']['reason']

    repo.items[57]['status'] = 'in_stock'
    out = await nodes.execute_action(turn(pending_action=pending, confirmed=True))
    assert out['action_result']['status'] == 'reserved' and repo.reserved == [57]


# ---------- answer ----------

async def test_compose_sees_only_available_copies_and_notes():
    nodes, llm, _ = make(AnswerDraft(message='ok', featured_item_ids=[57]))
    state = turn('jazz-funk under $40', available_items=[HEAD_HUNTERS],
                 asked={'query_text': 'x', 'max_price': 40.0}, used={'query_text': 'x', 'max_price': 50.0})
    out = await nodes.compose_answer(state)
    assert out['answer_attempts'] == 1 and out['draft']['featured_item_ids'] == [57]
    prompt = llm.prompts('AnswerDraft')[0]
    assert '"id": 57' in prompt and 'Black Byrd' not in prompt
    assert 'price limit was raised to $50' in prompt


async def test_verify_rejects_invented_price_and_unavailable_item():
    nodes, _, _ = make()
    state = turn(available_items=[HEAD_HUNTERS],
                 draft={'message': 'Head Hunters, only $25!', 'featured_item_ids': [57, 99], 'follow_up_question': None})
    issues = (await nodes.verify_answer(state))['answer_issues']
    assert any('99' in i for i in issues) and any('$25' in i for i in issues)


async def test_verify_pass_sets_answer_shown_ids_and_numbered_history():
    nodes, _, _ = make()
    state = turn(available_items=[HEAD_HUNTERS, BLACK_BYRD], shown_item_ids=[1, 2],
                 draft={'message': 'Two great picks, from $32.', 'featured_item_ids': [58, 57],
                        'follow_up_question': 'Original or reissue?'})
    out = await nodes.verify_answer(state)
    assert out['shown_item_ids'] == [58, 57] and out['answer_issues'] == []
    assert out['answer'].endswith('Original or reissue?')
    history = out['messages'][0].content
    assert f'1. {card_label(BLACK_BYRD)}\n2. {card_label(HEAD_HUNTERS)}' in history


async def test_fallback_uses_only_real_data():
    nodes, llm, _ = make()
    out = await nodes.fallback_template(turn(available_items=[HEAD_HUNTERS]))
    assert out['shown_item_ids'] == [57] and '$38.00' in out['messages'][0].content and llm.calls == []
    out = await nodes.fallback_template(turn(available_items=[]))
    assert 'nothing in stock matches' in out['answer']


# ---------- information ----------

async def test_collection_insights_lists_missing_albums_once_each():
    extra = copy_row(62, 9, 'Fleetwood Mac', 'Tusk', price='40.00', genres=('rock',))
    nodes, _, repo = make(items=(RUMOURS, TUSK, extra, HEAD_HUNTERS))
    repo.stats = {'total': 1, 'by_genre': {'rock': 1}, 'by_decade': {'1970s': 1}, 'records': ['Fleetwood Mac — Rumours']}
    out = await nodes.collection_insights(turn(asked={'query_text': 'x', 'artists': ['Fleetwood Mac']}))
    assert [i['title'] for i in out['available_items']] == ['Tusk']   # Rumours owned; one Tusk copy (the $40 one fits the cap)
    assert out['catalog_details'][0]['collection_stats']['total'] == 1
