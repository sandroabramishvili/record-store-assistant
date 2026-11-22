"""The whole graph, stage by stage in the guide's build order (10.4), with FakeLLM + FakeRepo.

Each test runs the compiled graph (real wiring, real routing, real interrupt) and checks
which path it took, what the LLM saw and what ended up in the state.
"""

import itertools

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.ai.deps import Deps
from app.ai.graph import build_graph
from app.ai.prompts import REFUSAL_MESSAGE
from app.ai.schemas import AnswerDraft, RequestAnalysis, SearchConstraints
from app.ai.state import new_turn_input
from app.services.customer import CustomerContext
from tests.graph.fakes import FakeLLM, FakeRepo, copy_row

HEAD_HUNTERS = copy_row(57, 5, 'Herbie Hancock', 'Head Hunters', price='38.00', genres=('jazz', 'funk'))
BLACK_BYRD = copy_row(58, 6, 'Donald Byrd', 'Black Byrd', price='32.00')
RUMOURS = copy_row(60, 8, 'Fleetwood Mac', 'Rumours', price='30.00', genres=('rock',))
TUSK = copy_row(61, 9, 'Fleetwood Mac', 'Tusk', price='28.00', genres=('rock',))
CUSTOMER = CustomerContext(profile={'min_condition': 'VG'},
                           collection=[{'record_id': 8, 'artist_norm': 'fleetwood mac', 'title_norm': 'rumours'}])
_threads = itertools.count()


def setup(*responses, items=(HEAD_HUNTERS, BLACK_BYRD, RUMOURS, TUSK)):
    llm, repo = FakeLLM(*responses), FakeRepo(items, CUSTOMER)
    graph = build_graph(Deps(llm=llm, repo=repo), checkpointer=InMemorySaver())
    config = {'configurable': {'thread_id': f'test-{next(_threads)}'}}
    return graph, llm, repo, config


async def ask(graph, config, text):
    return await graph.ainvoke(new_turn_input(text, user_id=1), config)


def understood(intent='find_record', ref=None, **c):
    return RequestAnalysis(intent=intent, constraints=SearchConstraints(query_text='x', **c),
                           refers_to_previous=ref is not None, reference_index=ref)


def draft(message, *ids, follow_up=None):
    return AnswerDraft(message=message, featured_item_ids=list(ids), follow_up_question=follow_up)


# ---------- A: happy path ----------

async def test_a_happy_path_find_record():
    graph, llm, _, config = setup(understood(max_price=40), draft('Two jazz-funk gems for you.', 57, 58))
    state = await ask(graph, config, 'jazz-funk under $40')
    assert state['answer'] == 'Two jazz-funk gems for you.'
    assert state['shown_item_ids'] == [57, 58]
    assert [name for name, _ in llm.calls] == ['RequestAnalysis', 'AnswerDraft']   # exactly two LLM calls


async def test_a_off_topic_is_refused_without_a_second_llm_call():
    graph, llm, repo, config = setup(understood('off_topic'))
    state = await ask(graph, config, "What's the weather in Tbilisi?")
    assert state['answer'] == REFUSAL_MESSAGE and len(llm.calls) == 1
    assert repo.searches == [] and repo.logs == []                       # nothing touched


# ---------- B: verification ----------

async def test_b_rejected_draft_is_retried_with_the_issues():
    graph, llm, _, config = setup(understood(), draft('Head Hunters, only $5!', 57), draft('Head Hunters, $38.00.', 57))
    state = await ask(graph, config, 'jazz-funk')
    assert state['answer'] == 'Head Hunters, $38.00.' and state['answer_attempts'] == 2
    retry_prompt = llm.prompts('AnswerDraft')[1]
    assert '<answer_issues>' in retry_prompt and '$5' in retry_prompt


async def test_verify_fallback_after_two_failures():
    """Spec test: two failed verifications → fallback_template; the answer contains only real data."""
    graph, llm, _, config = setup(understood(max_price=40), draft('Only $5!', 57), draft('We have item 999.', 999),
                                  items=(HEAD_HUNTERS, BLACK_BYRD))
    state = await ask(graph, config, 'jazz-funk')
    assert '$5' not in state['answer'] and 'Here is what we have in stock' in state['answer']
    assert set(state['shown_item_ids']) == {57, 58}
    assert '$38.00' in state['messages'][-1].content                      # real prices from the DB
    assert len(llm.calls) == 3                                            # understand + 2 drafts, no 4th


async def test_b_recommendation_without_cards_is_rejected():
    """Found in the first real run: "like these!" with nothing featured."""
    graph, llm, _, config = setup(understood(), draft('You might enjoy records like these!'), draft('Try these.', 57))
    state = await ask(graph, config, 'something upbeat')
    assert state['shown_item_ids'] == [57] and state['answer_attempts'] == 2
    assert 'none was featured' in llm.prompts('AnswerDraft')[1]


# ---------- C: relax loop ----------

async def test_c_relax_raises_price_and_the_answer_says_so():
    graph, llm, repo, config = setup(understood(max_price=30), draft('Black Byrd at $32.00.', 58),
                                     items=(HEAD_HUNTERS, BLACK_BYRD))       # nothing ≤ $30
    state = await ask(graph, config, 'jazz under $30')
    assert [s.get('max_price') for s in repo.searches] == [30, 37.5]
    assert state['relax_attempts'] == 1 and state['request_constraints']['max_price'] == 30
    assert 'price limit was raised to $37.5' in llm.prompts('AnswerDraft')[0]


async def test_relax_loop_limit():
    """Spec test: the relax loop runs at most 2 times, then goes to offer_wantlist."""
    graph, _, repo, config = setup(understood(max_price=5, decade=1970, genres=['jazz-funk'], artists=['Nirvana'],
                                              titles=['Nevermind']), items=())
    state = await ask(graph, config, 'Nevermind original?')
    assert len(repo.searches) == 3                                        # original + 2 relaxed
    question = state['__interrupt__'][0].value
    assert question['type'] == 'confirmation_required' and question['action'] == 'wantlist'
    assert 'Nirvana – Nevermind' in question['question']
    assert repo.logs == [('find_record', 0, 2)]


async def test_c_nothing_to_loosen_skips_pointless_searches():
    graph, _, repo, config = setup(understood(), draft('Sorry, nothing right now.'), items=())
    state = await ask(graph, config, '70s soul')                         # no artist → no wantlist offer
    assert len(repo.searches) == 1 and '__interrupt__' not in state
    assert state['answer'] == 'Sorry, nothing right now.'


# ---------- D, E: information ----------

async def test_d_record_info_answers_from_knowledge():
    graph, llm, repo, config = setup(understood('record_info'), draft('VG+ means light signs of play.'))
    repo.chunks = [{'title': 'The Goldmine Grading Scale', 'text': 'VG+ ... an occasional soft tick', 'score': 0.8}]
    state = await ask(graph, config, 'What does VG+ mean?')
    assert state['answer'] == 'VG+ means light signs of play.' and repo.searches == []
    assert 'occasional soft tick' in llm.prompts('AnswerDraft')[0]


async def test_e_collection_help_lists_missing_albums():
    graph, llm, _, config = setup(understood('collection_help', artists=['Fleetwood Mac']), draft('You are missing Tusk.', 61))
    state = await ask(graph, config, 'Which Fleetwood Mac am I missing?')
    assert state['shown_item_ids'] == [61]
    prompt = llm.prompts('AnswerDraft')[0]
    assert '"title": "Tusk"' in prompt and '"title": "Rumours"' not in prompt


# ---------- F: Human-in-the-Loop ----------

async def two_turns_then_reserve(resume, *later):
    graph, llm, repo, config = setup(understood(), draft('Two picks.', 57, 58), understood('reserve', ref=2), *later)
    await ask(graph, config, 'jazz-funk')
    paused = await ask(graph, config, 'the second one sounds great, hold it for me')
    question = paused['__interrupt__'][0].value
    assert (question['type'], question['action']) == ('confirmation_required', 'reserve')
    assert question['inventory_item_id'] == 58 and question['price'] == '32.00'
    assert repo.reserved == []                                           # nothing before the answer
    return await graph.ainvoke(Command(resume=resume), config), repo, llm


async def test_reservation_requires_confirmation():
    """Spec test: nothing is reserved until the customer confirms; on cancel nothing is written."""
    state, repo, _ = await two_turns_then_reserve({'confirmed': False}, draft('No problem, nothing reserved.'))
    assert repo.reserved == [] and state['action_result']['status'] == 'cancelled'

    state, repo, llm = await two_turns_then_reserve({'confirmed': True}, draft('Done, Black Byrd is yours to collect.'))
    assert repo.reserved == [58] and state['action_result']['status'] == 'reserved'
    assert 'Reservation confirmed' in llm.prompts('AnswerDraft')[-1]


async def test_interrupt_and_resume():
    """Spec test: the graph pauses and finishes after resume.

    Guide 10.6: on resume the paused node runs again from the top. Only confirm_action
    re-runs; the checks in prepare_reservation and the write in execute_action happen once.
    """
    graph, _, repo, config = setup(understood(), draft('Two picks.', 57, 58),
                                   understood('reserve', ref=1), draft('Reserved!'))
    await ask(graph, config, 'jazz-funk')
    paused = await ask(graph, config, 'the first one please')

    snapshot = await graph.aget_state(config)
    assert snapshot.next == ('confirm_action',) and '__interrupt__' in paused   # paused, waiting
    assert repo.calls == {'active_reservation_count': 1}

    final = await graph.ainvoke(Command(resume={'confirmed': True}), config)
    assert final['answer'] == 'Reserved!' and (await graph.aget_state(config)).next == ()
    assert repo.calls == {'active_reservation_count': 1, 'reserve': 1}          # nothing ran twice


async def test_interrupt_needs_a_checkpointer():
    """Without a checkpointer the graph pauses, but the customer's answer can never resume it."""
    llm, repo = FakeLLM(understood('reserve', ref=1)), FakeRepo((HEAD_HUNTERS,), CUSTOMER)
    graph = build_graph(Deps(llm=llm, repo=repo), checkpointer=None)
    paused = await graph.ainvoke(new_turn_input('the first one', 1) | {'shown_item_ids': [57]})
    assert '__interrupt__' in paused
    with pytest.raises(RuntimeError, match='without checkpointer'):
        await graph.ainvoke(Command(resume={'confirmed': True}))
    assert repo.reserved == []


async def test_reference_out_of_range_asks_instead_of_reserving():
    graph, _, repo, config = setup(understood(), draft('Two picks.', 57, 58), understood('reserve', ref=7),
                                   draft('Which one do you mean, 1 or 2?'))
    await ask(graph, config, 'jazz-funk')
    state = await ask(graph, config, 'the seventh one')
    assert '__interrupt__' not in state and repo.reserved == []
    assert state['answer'] == 'Which one do you mean, 1 or 2?'
    assert state['shown_item_ids'] == [57, 58]                          # list kept for the next try
