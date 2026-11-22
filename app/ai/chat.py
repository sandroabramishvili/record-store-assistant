"""Running the graph for one HTTP request and streaming it as Server-Sent Events (guide 11.3).

One chat session = one LangGraph thread: thread_id = f'chat-{session.id}' (spec 3).

What the customer sees (spec 7.7), one `data: {...}` line per event:
    {"type": "node", "name": "search_inventory"}         progress, while the graph runs
    {"type": "confirmation_required", "action": ...}     the graph paused (interrupt)
    {"type": "answer", "message": ..., "items": [...]}   the VERIFIED answer, sent once
    {"type": "error", "message": ...}                    something failed; the customer's message is saved
    {"type": "done"}                                     always last

The LLM's tokens are never streamed: verify_answer checks the whole answer first, so an
invented price never reaches the screen (spec 7.7 note).

ChatMessage rows (the UI's history) are written here, each in its OWN database session:
the response streams after the endpoint has returned, when the request's session is closed.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy import func, update

from app.ai.indexing import rate_limit_delay
from app.ai.tracing import tag_run, tracing_callbacks
from app.config import settings
from app.db import SessionLocal
from app.models import ChatMessage, ChatSession, User

logger = logging.getLogger(__name__)

DONE = {'type': 'done'}
SSE_HEADERS = {'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}   # no proxy buffering


def thread_id(session_id: int) -> str:
    return f'chat-{session_id}'


def make_config(session_id: int, user: User) -> RunnableConfig:
    """thread_id for the checkpointer + the LangSmith trace setup (ids only, never emails).

    `run_id` is chosen here so the root run can be tagged with the intent afterwards
    (it is only known once understand_request has run). Tags and metadata are inherited
    by every node and LLM call, so any of them can be filtered by session in LangSmith.
    """
    return {
        'configurable': {'thread_id': thread_id(session_id)},
        'run_id': uuid4(),
        'run_name': 'chat_turn',
        'callbacks': tracing_callbacks(),
        'tags': ['chat', f'env:{settings.environment}'],
        'metadata': {'session_id': session_id, 'user_id': user.id, 'role': user.role,
                     'environment': settings.environment},
    }


def tag_with_intent(config: RunnableConfig, values: dict) -> None:
    """Spec 9: session_id + environment are metadata from the start; the intent becomes a tag
    (`intent:find_record`) on the root run once the run is over."""
    intent = values.get('intent') or 'unknown'
    tag_run(config['run_id'], [*config['tags'], f'intent:{intent}'])


def sse(data: dict) -> str:
    return f'data: {json.dumps(data, default=str)}\n\n'


def answer_cards(values: dict) -> list[dict]:
    """The copies featured in THIS turn's answer, in order, as cards.

    `shown_item_ids` keeps the previous list when an answer features nothing new (so
    "the second one" still works), so only ids found in this turn's data become cards.
    """
    cards: dict[int, dict] = {}
    for i in values.get('available_items') or []:
        cards[i['item_id']] = {
            'id': i['item_id'], 'artist': i['artist'], 'title': i['title'], 'year': i['original_year'],
            'pressing': f'{i["release_year"]} reissue' if i['is_reissue'] else 'original',
            'media_condition': i['media_condition'], 'sleeve_condition': i['sleeve_condition'], 'price': i['price'],
        }
    for d in values.get('catalog_details') or []:            # record_info answers about a specific pressing
        for c in d.get('copies_in_stock', []):
            cards[c['id']] = {
                'id': c['id'], 'artist': d['artist'], 'title': d['title'], 'year': d['original_year'],
                'pressing': d['pressing'] if d['pressing'] == 'original' else f'{d["release_year"]} reissue',
                'media_condition': c['media_condition'], 'sleeve_condition': c['sleeve_condition'], 'price': c['price'],
            }
    return [cards[i] for i in values.get('shown_item_ids') or [] if i in cards]


async def pending_confirmation(graph, config: RunnableConfig) -> dict | None:
    """The question the graph is paused on, or None."""
    snapshot = await graph.aget_state(config)
    return snapshot.interrupts[0].value if snapshot.interrupts else None


def turn_intent(graph_input: Any, values: dict) -> str | None:
    """The intent to store on the assistant's reply (for /analytics/intents): only for a NEW
    message. A resumed run (the answer after "yes"/"no") belongs to a turn already counted.
    A turn that failed before understand_request finished has no intent ('' → None)."""
    return None if isinstance(graph_input, Command) else (values.get('intent') or None)


async def save_message(session_id: int, role: str, content: str, item_ids: list[int] = (),
                       intent: str | None = None) -> None:
    """A history row + bump the session's updated_at (so the chat list is sorted by activity)."""
    async with SessionLocal() as db:
        db.add(ChatMessage(session_id=session_id, role=role, content=content, inventory_item_ids=list(item_ids),
                           intent=intent or None))
        await db.execute(update(ChatSession).where(ChatSession.id == session_id).values(updated_at=func.now()))
        await db.commit()


def error_message(error: Exception, values: dict) -> str:
    """A clear message for the customer. Says so if the action already went through before the failure."""
    overloaded = '503' in str(error) and 'UNAVAILABLE' in str(error)      # Gemini "high demand" (seen for real)
    if rate_limit_delay(error) is not None or overloaded:
        text = 'The assistant is busy right now (too many requests). Your message is saved; please try again in a minute.'
    elif isinstance(error, TimeoutError):
        text = 'The assistant took too long to answer. Your message is saved; please try again.'
    else:
        text = 'The assistant is unavailable right now. Your message is saved; please try again in a moment.'
    done = values.get('action_result') or {}
    if done.get('status') == 'reserved':
        text = f'Your reservation of {done.get("label")} is confirmed (see My reservations). ' + text
    elif done.get('status') == 'added':
        text = 'It was added to your wantlist. ' + text
    return text


# ---------- one run per chat at a time ----------
#
# Two requests for the same chat must not run the graph at the same time: a double-clicked
# "Confirm" would resume the same pause twice (the 2nd reservation then fails and the
# customer sees "failed" for a hold that worked). In-process registry: correct for the one
# uvicorn worker this app runs with (Docker, Phase 17). With several workers it would need a
# Postgres advisory lock instead.
_busy: dict[int, float] = {}           # session id → monotonic time its run started


def busy_stale_after() -> float:
    """A claim older than this is ignored: covers a response that was never streamed (the
    client left before the body started), so a chat can't stay locked forever."""
    return settings.chat_turn_timeout_seconds + 30


def claim_session(session_id: int) -> bool:
    """True and marks the chat busy; False if a run for it is still going. No await inside:
    check-and-set is atomic on the event loop."""
    now = time.monotonic()
    started = _busy.get(session_id)
    if started is not None and now - started < busy_stale_after():
        return False
    _busy[session_id] = now
    return True


def release_session(session_id: int) -> None:
    _busy.pop(session_id, None)


async def _updates_with_deadline(graph, graph_input: Any, config: RunnableConfig, timeout: float):
    """graph.astream(updates), but the WHOLE turn must finish within `timeout` seconds.

    The deadline wraps only the `await` for the next update, never a `yield`, and runs in the
    same task (no wait_for → no new task), so LangGraph's context is untouched. On timeout
    asyncio raises TimeoutError, handled like any other failure (message kept, clear error).
    """
    stream = graph.astream(graph_input, config, stream_mode='updates')
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    update = await anext(stream)
            except StopAsyncIteration:
                return
            yield update
    finally:
        await stream.aclose()


async def stream_turn(graph, graph_input: Any, config: RunnableConfig, session_id: int,
                      *, timeout: float | None = None) -> AsyncIterator[str]:
    """Run the graph (a new message, or Command(resume=...)) and yield SSE events.
    Releases the chat's busy claim (claim_session) when it ends, however it ends."""
    try:
        async for event in _stream_turn(graph, graph_input, config, session_id,
                                        settings.chat_turn_timeout_seconds if timeout is None else timeout):
            yield event
    finally:
        release_session(session_id)


async def _stream_turn(graph, graph_input: Any, config: RunnableConfig, session_id: int,
                       timeout: float) -> AsyncIterator[str]:
    try:
        question = None
        # Always consume the stream to its END, even after the interrupt: leaving the loop
        # early closes the run before the checkpointer has written the paused state
        # (the Postgres saver writes in the background), and the confirmation would be lost.
        async for update in _updates_with_deadline(graph, graph_input, config, timeout):
            for node, payload in update.items():
                if node == '__interrupt__':                  # paused in confirm_action
                    question = payload[0].value
                else:
                    yield sse({'type': 'node', 'name': node})

        values = (await graph.aget_state(config)).values
        tag_with_intent(config, values)
        intent = turn_intent(graph_input, values)

        if question is not None:
            item = question.get('inventory_item_id')
            await save_message(session_id, 'assistant', question['question'], [item] if item else [], intent)
            yield sse(question)
            yield sse(DONE)
            return

        cards = answer_cards(values)
        await save_message(session_id, 'assistant', values['answer'], [c['id'] for c in cards], intent)
        yield sse({'type': 'answer', 'message': values['answer'], 'items': cards,
                   'action_result': values.get('action_result')})
    except Exception as error:                               # LLM timeout / 429 / DB: never a broken stream
        logger.exception('Graph run failed for chat session %s', session_id)
        try:
            values = (await graph.aget_state(config)).values
        except Exception:
            values = {}
        tag_with_intent(config, values)                      # failed runs are filterable by intent too
        message = error_message(error, values)
        try:
            await save_message(session_id, 'assistant', message, intent=turn_intent(graph_input, values))
        except Exception:
            logger.exception('Could not save the error message for chat session %s', session_id)
        yield sse({'type': 'error', 'message': message})
    yield sse(DONE)
