"""The whole graph against the REAL repository and the seeded test database (only the LLM is fake).

Checks what FakeRepo can't: SearchLog rows (10.8), real reservations and wantlist entries
written after a confirmation, and real store rules on real data.
"""

import pytest_asyncio
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import select

from app.ai.deps import Deps
from app.ai.graph import build_graph
from app.ai.schemas import AnswerDraft, RequestAnalysis, SearchConstraints
from app.ai.state import new_turn_input
from app.ai.vectorstores import get_knowledge_store, get_record_store
from app.db import SessionLocal
from app.models import InventoryItem, Reservation, SearchLog, User, WantlistItem
from app.repositories.store import StoreRepository
from scripts.seed_catalog import seed_catalog
from tests.graph.fakes import FakeLLM, feature_available


@pytest_asyncio.fixture
async def jazz_fan():
    await seed_catalog()
    async with SessionLocal() as db:
        return await db.scalar(select(User.id).where(User.email == 'jazz.fan@example.com'))


def graph_with(*responses):
    llm = FakeLLM(*responses)
    repo = StoreRepository(SessionLocal, get_record_store(), get_knowledge_store())
    return build_graph(Deps(llm=llm, repo=repo), checkpointer=InMemorySaver()), llm


def understood(intent='find_record', ref=None, **c):
    return RequestAnalysis(intent=intent, constraints=SearchConstraints(query_text='x', **c),
                           refers_to_previous=ref is not None, reference_index=ref)


async def rows(model, *where):
    async with SessionLocal() as db:
        return list((await db.scalars(select(model).where(*where))).all())


JAZZ_FUNK = dict(genres=['jazz-funk'], decade=1970, max_price=40, min_condition='VG+', original_pressing_only=True)


async def test_search_is_logged_with_what_the_customer_asked(jazz_fan):
    graph, _ = graph_with(understood(**JAZZ_FUNK), feature_available('Three originals.'))
    config = {'configurable': {'thread_id': 'db-1'}}
    state = await graph.ainvoke(new_turn_input('70s jazz-funk under $40', jazz_fan), config)

    titles = {i['title'] for i in state['available_items']}
    assert titles == {'Head Hunters', 'Black Byrd', 'Everybody Loves the Sunshine'}   # real data, real rules
    [log] = await rows(SearchLog)
    assert (log.user_id, log.intent, log.results_count, log.relax_attempts) == (jazz_fan, 'find_record', 3, 0)
    assert log.constraints['max_price'] == 40 and log.constraints['genres'] == ['jazz-funk']


async def test_zero_result_search_is_logged_after_relaxing_and_wantlist_written_on_yes(jazz_fan):
    graph, _ = graph_with(understood(titles=['Nevermind'], original_pressing_only=True, decade=1990),
                          AnswerDraft(message='Added to your wantlist.'))
    config = {'configurable': {'thread_id': 'db-2'}}
    paused = await graph.ainvoke(new_turn_input('Nevermind original?', jazz_fan), config)
    question = paused['__interrupt__'][0].value
    assert question['action'] == 'wantlist' and question['artist'] == 'Nirvana'    # artist found in the catalog

    [log] = await rows(SearchLog)
    assert (log.results_count, log.relax_attempts) == (0, 1)                       # unmet demand, for staff analytics
    assert await rows(WantlistItem, WantlistItem.user_id == jazz_fan, WantlistItem.title_norm == 'nevermind') == []

    await graph.ainvoke(Command(resume={'confirmed': True}), config)
    [entry] = await rows(WantlistItem, WantlistItem.user_id == jazz_fan, WantlistItem.title_norm == 'nevermind')
    assert entry.artist == 'Nirvana' and entry.matched_item_id is None             # only copy is sold


async def test_second_one_is_really_reserved_in_the_database(jazz_fan):
    graph, _ = graph_with(understood(**JAZZ_FUNK), feature_available('Three originals.'),
                          understood('reserve', ref=2), AnswerDraft(message='Reserved for you.'))
    config = {'configurable': {'thread_id': 'db-3'}}
    first = await graph.ainvoke(new_turn_input('70s jazz-funk under $40', jazz_fan), config)
    second_id = first['shown_item_ids'][1]

    await graph.ainvoke(new_turn_input('hold the second one', jazz_fan), config)
    assert await rows(Reservation, Reservation.inventory_item_id == second_id) == []   # not before "yes"
    final = await graph.ainvoke(Command(resume={'confirmed': True}), config)

    [res] = await rows(Reservation, Reservation.inventory_item_id == second_id)
    assert (res.user_id, res.status) == (jazz_fan, 'active')
    [copy] = await rows(InventoryItem, InventoryItem.id == second_id)
    assert copy.status == 'reserved' and final['action_result']['status'] == 'reserved'
