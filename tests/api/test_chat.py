"""Chat endpoints (Phase 11) over HTTP: real app, real Postgres checkpointer, real repository.

Only the LLM is fake (spec 12). Covers the Phase 11 "Done when" list:
- search → "hold the second one" → confirm → the reservation exists in the DB
- an API restart while a confirmation is pending (new graph + new pool) → confirming still works
- someone else's session → 404
plus: 409 rules, the LLM-failure path (message kept, clear error), and delete removing checkpoints.
"""

import asyncio
import json
import time
from datetime import timedelta

import pytest_asyncio
from sqlalchemy import func, select, text

from app.ai.chat import claim_session, release_session
from app.ai.checkpointer import open_checkpointer
from app.ai.deps import Deps
from app.ai.graph import build_graph
from app.ai.schemas import AnswerDraft, RequestAnalysis
from app.config import settings
from app.schemas.chat import MAX_MESSAGE_CHARS
from app.ai.vectorstores import get_knowledge_store, get_record_store
from app.auth.security import create_access_token
from app.db import SessionLocal, engine
from app.main import app
from app.models import ChatMessage, Reservation, User
from app.repositories.store import StoreRepository
from scripts.seed_catalog import seed_catalog
from tests.api.test_graph_db import JAZZ_FUNK, understood
from tests.graph.fakes import FakeLLM, Reply, feature_available


def make_graph(llm, checkpointer):
    repo = StoreRepository(SessionLocal, get_record_store(), get_knowledge_store())
    return build_graph(Deps(llm=llm, repo=repo), checkpointer)


@pytest_asyncio.fixture
async def llm():
    """The app's graph as the lifespan builds it (Postgres checkpointer), but with a fake LLM."""
    fake = FakeLLM()
    async with open_checkpointer(max_size=2) as checkpointer:
        app.state.graph = make_graph(fake, checkpointer)
        yield fake
        app.state.graph = None


@pytest_asyncio.fixture
async def jazz_fan():
    """The seeded customer (owns Kind of Blue etc.), with a token."""
    await seed_catalog()
    async with SessionLocal() as db:
        user_id = await db.scalar(select(User.id).where(User.email == 'jazz.fan@example.com'))
    return {'Authorization': f'Bearer {create_access_token(user_id, "customer")}'}


def events(response) -> list[dict]:
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/event-stream')
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


async def new_chat(client, headers) -> int:
    r = await client.post('/chat/sessions', headers=headers)
    assert r.status_code == 201, r.text
    return r.json()['id']


async def say(client, headers, sid, text_):
    return await client.post(f'/chat/sessions/{sid}/messages', headers=headers, json={'content': text_})


async def search_then_hold_second(client, headers, llm) -> tuple[int, int]:
    """Turn 1: three jazz-funk originals. Turn 2: "hold the second one" → paused. Returns (session, item id)."""
    llm.add(understood(**JAZZ_FUNK), feature_available('Three originals.'), understood('reserve', ref=2))
    sid = await new_chat(client, headers)
    first = events(await say(client, headers, sid, '70s jazz-funk under $40'))
    answer = next(e for e in first if e['type'] == 'answer')
    assert len(answer['items']) == 3
    second = answer['items'][1]['id']

    paused = events(await say(client, headers, sid, 'Hold the second one'))
    assert [e['type'] for e in paused][-2:] == ['confirmation_required', 'done']
    assert paused[-2]['action'] == 'reserve' and paused[-2]['inventory_item_id'] == second
    return sid, second


async def reservations_for(item_id):
    async with SessionLocal() as db:
        return (await db.scalars(select(Reservation).where(Reservation.inventory_item_id == item_id))).all()


# ---------- the full conversation ----------


async def test_full_conversation_search_hold_confirm(client, llm, jazz_fan):
    sid, second = await search_then_hold_second(client, jazz_fan, llm)
    assert await reservations_for(second) == []                              # nothing before "yes"

    # a new message while the question is open → 409 (and it is NOT saved)
    r = await say(client, jazz_fan, sid, 'Actually, anything by Miles?')
    assert r.status_code == 409

    history = (await client.get(f'/chat/sessions/{sid}/messages', headers=jazz_fan)).json()
    assert history['session']['title'] == '70s jazz-funk under $40'           # first message = title
    assert history['pending_confirmation']['action'] == 'reserve'            # the UI can redraw the buttons
    assert [m['role'] for m in history['messages']] == ['user', 'assistant', 'user', 'assistant']
    assert len(history['messages'][1]['items']) == 3
    assert history['messages'][3]['items'][0]['id'] == second

    llm.add(AnswerDraft(message='Done, it is held for you.'))
    done = events(await client.post(f'/chat/sessions/{sid}/confirm', headers=jazz_fan, json={'approved': True}))
    assert done[-1] == {'type': 'done'}
    answer = next(e for e in done if e['type'] == 'answer')
    assert answer['action_result']['status'] == 'reserved'
    nodes = [e['name'] for e in done if e['type'] == 'node']
    assert nodes[:2] == ['confirm_action', 'execute_action']                 # resumed where it paused

    [reservation] = await reservations_for(second)
    assert reservation.status == 'active'

    history = (await client.get(f'/chat/sessions/{sid}/messages', headers=jazz_fan)).json()
    assert history['pending_confirmation'] is None
    assert history['messages'][4]['content'] == 'Yes, please.'
    reserved_card = history['messages'][1]['items'][1]
    assert reserved_card['id'] == second and reserved_card['status'] == 'reserved'   # current status

    r = await client.post(f'/chat/sessions/{sid}/confirm', headers=jazz_fan, json={'approved': True})
    assert r.status_code == 409                                              # nothing waiting any more


async def test_no_reserves_nothing(client, llm, jazz_fan):
    sid, second = await search_then_hold_second(client, jazz_fan, llm)
    llm.add(AnswerDraft(message='No problem.'))
    done = events(await client.post(f'/chat/sessions/{sid}/confirm', headers=jazz_fan, json={'approved': False}))
    assert next(e for e in done if e['type'] == 'answer')['action_result']['status'] == 'cancelled'
    assert await reservations_for(second) == []


async def test_confirmation_survives_an_api_restart(client, llm, jazz_fan):
    sid, second = await search_then_hold_second(client, jazz_fan, llm)

    # "Restart": a brand-new graph object and connection pool. Nothing is shared with the
    # old process except the database, so the paused state must come from Postgres.
    fresh = FakeLLM(AnswerDraft(message='Held for 48 hours.'))
    async with open_checkpointer(max_size=2) as checkpointer:
        app.state.graph = make_graph(fresh, checkpointer)
        done = events(await client.post(f'/chat/sessions/{sid}/confirm', headers=jazz_fan, json={'approved': True}))
    assert next(e for e in done if e['type'] == 'answer')['action_result']['status'] == 'reserved'
    [reservation] = await reservations_for(second)
    assert reservation.status == 'active'


async def test_every_turn_is_counted_once_by_intent(client, llm, jazz_fan):
    """Phase 15: /analytics/intents counts customer REQUESTS. The answer after "yes" belongs
    to the reservation turn already counted; an off-topic turn counts although it never searches."""
    sid, _ = await search_then_hold_second(client, jazz_fan, llm)       # find_record, then reserve (paused)
    llm.add(AnswerDraft(message='Held.'), understood('off_topic'))
    events(await client.post(f'/chat/sessions/{sid}/confirm', headers=jazz_fan, json={'approved': True}))
    events(await say(client, jazz_fan, sid, 'weather?'))

    async with SessionLocal() as db:                                     # the seeded staff account
        staff_id = await db.scalar(select(User.id).where(User.email == 'staff@example.com'))
    staff = {'Authorization': f'Bearer {create_access_token(staff_id, "staff")}'}
    body = (await client.get('/analytics/intents', headers=staff)).json()
    assert body['total'] == 3
    assert {i['name']: i['count'] for i in body['intents']} == {'find_record': 1, 'reserve': 1, 'off_topic': 1}


# ---------- ownership ----------


async def test_user_cannot_read_others_chat(client, llm, jazz_fan, other_customer):
    """Spec test: someone else's chat → 404 (reading, writing, confirming, deleting, listing)."""
    sid = await new_chat(client, jazz_fan)
    bob = other_customer.headers
    assert (await client.get(f'/chat/sessions/{sid}/messages', headers=bob)).status_code == 404
    assert (await say(client, bob, sid, 'hi')).status_code == 404
    assert (await client.post(f'/chat/sessions/{sid}/confirm', headers=bob, json={'approved': True})).status_code == 404
    assert (await client.delete(f'/chat/sessions/{sid}', headers=bob)).status_code == 404
    assert (await client.get('/chat/sessions', headers=bob)).json()['total'] == 0
    assert llm.calls == []                                                   # the graph never ran


async def test_list_is_mine_newest_activity_first(client, llm, jazz_fan):
    first = await new_chat(client, jazz_fan)
    second = (await client.post('/chat/sessions', headers=jazz_fan, json={'title': 'Gift ideas'})).json()['id']
    llm.add(understood('off_topic'))
    events(await say(client, jazz_fan, first, 'weather?'))                   # activity bumps `first` to the top
    page = (await client.get('/chat/sessions', headers=jazz_fan)).json()
    assert [s['id'] for s in page['items']] == [first, second]
    assert page['items'][1]['title'] == 'Gift ideas'


# ---------- failures ----------


async def test_llm_failure_keeps_the_message_and_says_so(client, llm, jazz_fan):
    sid = await new_chat(client, jazz_fan)
    llm.add(TimeoutError('gemini timed out'))                                 # network errors: not retried by us
    out = events(await say(client, jazz_fan, sid, 'Any Coltrane?'))
    assert [e['type'] for e in out][-2:] == ['error', 'done']
    assert 'Your message is saved' in out[-2]['message']

    messages = (await client.get(f'/chat/sessions/{sid}/messages', headers=jazz_fan)).json()['messages']
    assert [(m['role'], m['content']) for m in messages][0] == ('user', 'Any Coltrane?')

    # The next message works normally: a new input starts a fresh run from the checkpoint.
    llm.add(understood('off_topic'))
    out = events(await say(client, jazz_fan, sid, 'Any Coltrane?'))
    assert any(e['type'] == 'answer' for e in out)


async def test_failure_after_the_reservation_tells_the_customer_it_went_through(client, llm, jazz_fan):
    sid, second = await search_then_hold_second(client, jazz_fan, llm)
    error = RuntimeError('429 RESOURCE_EXHAUSTED')
    error.schema = AnswerDraft
    llm.add(error)
    done = events(await client.post(f'/chat/sessions/{sid}/confirm', headers=jazz_fan, json={'approved': True}))
    message = next(e for e in done if e['type'] == 'error')['message']
    assert 'reservation' in message and 'confirmed' in message and 'too many requests' in message
    assert len(await reservations_for(second)) == 1


async def test_blank_or_too_long_message_is_rejected(client, llm, jazz_fan):
    sid = await new_chat(client, jazz_fan)
    assert (await say(client, jazz_fan, sid, '   ')).status_code == 422
    assert (await say(client, jazz_fan, sid, 'x' * (MAX_MESSAGE_CHARS + 1))).status_code == 422
    assert llm.calls == []


# ---------- Phase 14: limits ----------


async def add_user_messages(sid: int, count: int, age: timedelta = timedelta(0)) -> None:
    async with SessionLocal() as db:
        for n in range(count):
            db.add(ChatMessage(session_id=sid, role='user', content=f'old {n}',
                               created_at=func.now() - age))           # DB clock, like the limit itself
        await db.commit()


async def test_rate_limit_counts_the_last_hour_across_all_chats(client, llm, jazz_fan):
    limit = settings.chat_messages_per_hour
    first, second = await new_chat(client, jazz_fan), await new_chat(client, jazz_fan)
    await add_user_messages(first, limit, age=timedelta(hours=2))          # outside the window: don't count
    await add_user_messages(first, limit - 1, age=timedelta(minutes=50))   # inside, in ANOTHER chat

    llm.add(understood('off_topic'))
    assert any(e['type'] == 'answer' for e in events(await say(client, jazz_fan, second, 'weather?')))  # = the limit

    r = await say(client, jazz_fan, second, 'one more?')
    assert r.status_code == 429
    assert 500 < int(r.headers['Retry-After']) <= 600                       # oldest counted one leaves in ~10 min
    messages = (await client.get(f'/chat/sessions/{second}/messages', headers=jazz_fan)).json()['messages']
    assert [m['content'] for m in messages if m['role'] == 'user'] == ['weather?']   # the 429'd one isn't saved


async def test_one_run_per_chat_at_a_time(client, llm, jazz_fan):
    """A double-clicked send / confirm: the 2nd request gets 409 while the 1st is running."""
    sid = await new_chat(client, jazz_fan)
    assert claim_session(sid)                                   # a run is in progress for this chat
    try:
        r = await say(client, jazz_fan, sid, 'weather?')
        assert r.status_code == 409 and 'Still answering' in r.json()['detail']
    finally:
        release_session(sid)
    llm.add(understood('off_topic'))
    events(await say(client, jazz_fan, sid, 'weather?'))       # free again
    assert claim_session(sid)                                   # ...and released after the stream ended
    release_session(sid)


async def test_a_hanging_model_hits_the_turn_deadline(client, llm, jazz_fan, monkeypatch):
    monkeypatch.setattr(settings, 'chat_turn_timeout_seconds', 0.5)

    async def hang(_messages):
        await asyncio.sleep(30)

    sid = await new_chat(client, jazz_fan)
    llm.add(Reply(RequestAnalysis, hang))
    started = time.monotonic()
    out = events(await say(client, jazz_fan, sid, 'Any Coltrane?'))
    assert time.monotonic() - started < 5                        # not 30 s
    assert out[-2]['type'] == 'error' and 'took too long' in out[-2]['message']

    llm.add(understood('off_topic'))                             # the chat is usable right after
    assert any(e['type'] == 'answer' for e in events(await say(client, jazz_fan, sid, 'Any Coltrane?')))


# ---------- delete ----------


async def test_delete_removes_history_and_checkpoints(client, llm, jazz_fan):
    sid = await new_chat(client, jazz_fan)
    llm.add(understood('off_topic'))
    events(await say(client, jazz_fan, sid, 'weather?'))

    async def checkpoint_rows():
        async with engine.connect() as conn:
            return await conn.scalar(text('SELECT count(*) FROM checkpoints WHERE thread_id = :t'), {'t': f'chat-{sid}'})

    assert await checkpoint_rows() > 0
    assert (await client.delete(f'/chat/sessions/{sid}', headers=jazz_fan)).status_code == 204
    assert await checkpoint_rows() == 0
    async with SessionLocal() as db:
        assert (await db.scalars(select(ChatMessage).where(ChatMessage.session_id == sid))).all() == []
    assert (await client.get(f'/chat/sessions/{sid}/messages', headers=jazz_fan)).status_code == 404
