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

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy import func, update

from app.ai.indexing import rate_limit_delay
from app.config import settings
from app.db import SessionLocal
from app.models import ChatMessage, ChatSession, User

logger = logging.getLogger(__name__)

DONE = {'type': 'done'}
SSE_HEADERS = {'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}   # no proxy buffering


def thread_id(session_id: int) -> str:
    return f'chat-{session_id}'


def make_config(session_id: int, user: User) -> RunnableConfig:
    """thread_id for the checkpointer + tags/metadata for LangSmith (ids only, never emails)."""
    return {
        'configurable': {'thread_id': thread_id(session_id)},
        'run_name': 'chat_turn',
        'tags': ['chat', f'env:{settings.environment}'],
        'metadata': {'session_id': session_id, 'user_id': user.id, 'role': user.role},
    }


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


async def save_message(session_id: int, role: str, content: str, item_ids: list[int] = ()) -> None:
    """A history row + bump the session's updated_at (so the chat list is sorted by activity)."""
    async with SessionLocal() as db:
        db.add(ChatMessage(session_id=session_id, role=role, content=content, inventory_item_ids=list(item_ids)))
        await db.execute(update(ChatSession).where(ChatSession.id == session_id).values(updated_at=func.now()))
        await db.commit()


def error_message(error: Exception, values: dict) -> str:
    """A clear message for the customer. Says so if the action already went through before the failure."""
    if rate_limit_delay(error) is not None:
        text = 'The assistant is busy right now (too many requests). Your message is saved; please try again in a minute.'
    else:
        text = 'The assistant is unavailable right now. Your message is saved; please try again in a moment.'
    done = values.get('action_result') or {}
    if done.get('status') == 'reserved':
        text = f'Your reservation of {done.get("label")} is confirmed (see My reservations). ' + text
    elif done.get('status') == 'added':
        text = 'It was added to your wantlist. ' + text
    return text


async def stream_turn(graph, graph_input: Any, config: RunnableConfig, session_id: int) -> AsyncIterator[str]:
    """Run the graph (a new message, or Command(resume=...)) and yield SSE events."""
    try:
        question = None
        # Always consume the stream to its END, even after the interrupt: leaving the loop
        # early closes the run before the checkpointer has written the paused state
        # (the Postgres saver writes in the background), and the confirmation would be lost.
        async for update in graph.astream(graph_input, config, stream_mode='updates'):
            for node, payload in update.items():
                if node == '__interrupt__':                  # paused in confirm_action
                    question = payload[0].value
                else:
                    yield sse({'type': 'node', 'name': node})

        if question is not None:
            item = question.get('inventory_item_id')
            await save_message(session_id, 'assistant', question['question'], [item] if item else [])
            yield sse(question)
            yield sse(DONE)
            return

        values = (await graph.aget_state(config)).values
        cards = answer_cards(values)
        await save_message(session_id, 'assistant', values['answer'], [c['id'] for c in cards])
        yield sse({'type': 'answer', 'message': values['answer'], 'items': cards,
                   'action_result': values.get('action_result')})
    except Exception as error:                               # LLM timeout / 429 / DB: never a broken stream
        logger.exception('Graph run failed for chat session %s', session_id)
        try:
            values = (await graph.aget_state(config)).values
        except Exception:
            values = {}
        message = error_message(error, values)
        try:
            await save_message(session_id, 'assistant', message)
        except Exception:
            logger.exception('Could not save the error message for chat session %s', session_id)
        yield sse({'type': 'error', 'message': message})
    yield sse(DONE)
