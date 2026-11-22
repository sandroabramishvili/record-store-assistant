"""Chat (spec 7.7). One ChatSession = one LangGraph thread (`chat-{id}`).

The graph is built once in the app's lifespan (main.py) with the Postgres checkpointer and
reached through the `get_graph` dependency (tests override it with a fake-LLM graph).

Rules:
- user_id and thread_id come from the JWT and the URL, never from the body.
- Someone else's chat → 404 (same as a missing one).
- A message while a confirmation is pending → 409: the customer must answer it first.
  (LangGraph would otherwise silently drop the paused action; a 409 makes it explicit.)
- The customer's message is saved BEFORE the graph runs, so an LLM failure never loses it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from sqlalchemy import func, select

from app.ai.chat import SSE_HEADERS, make_config, pending_confirmation, save_message, stream_turn, thread_id
from app.ai.state import new_turn_input
from app.auth.deps import CurrentUser, DbSession
from app.auth.ownership import get_owned_or_404
from app.models import ChatMessage, ChatSession, InventoryItem, Record
from app.schemas.chat import ChatHistory, ChatItem, ConfirmIn, MessageIn, MessageOut, SessionCreate, SessionOut
from app.schemas.common import Page
from app.services.reservations import release_expired

router = APIRouter(prefix='/chat', tags=['chat'])

DEFAULT_TITLE = 'New chat'
TITLE_LENGTH = 60


def get_graph(request: Request):
    graph = getattr(request.app.state, 'graph', None)
    if graph is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, 'The assistant is not available.')
    return graph


Graph = Annotated[object, Depends(get_graph)]


def title_from(text: str) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= TITLE_LENGTH else text[:TITLE_LENGTH - 1].rstrip() + '…'


def _stream(graph, graph_input, config, session_id: int) -> StreamingResponse:
    return StreamingResponse(stream_turn(graph, graph_input, config, session_id),
                             media_type='text/event-stream', headers=SSE_HEADERS)


# ---------- sessions ----------


@router.post('/sessions', response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(user: CurrentUser, db: DbSession, data: SessionCreate | None = None) -> ChatSession:
    session = ChatSession(user_id=user.id, title=(data.title if data and data.title else DEFAULT_TITLE))
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@router.get('/sessions', response_model=Page[SessionOut])
async def list_sessions(
    user: CurrentUser,
    db: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> Page[SessionOut]:
    """My chats, most recently active first. Only ever the caller's own (staff too)."""
    mine = ChatSession.user_id == user.id
    total = await db.scalar(select(func.count()).select_from(ChatSession).where(mine))
    rows = await db.scalars(select(ChatSession).where(mine)
                            .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
                            .offset((page - 1) * size).limit(size))
    return Page(items=[SessionOut.model_validate(s) for s in rows], total=total, page=page, size=size)


@router.get('/sessions/{session_id}/messages', response_model=ChatHistory)
async def history(session_id: int, user: CurrentUser, db: DbSession, graph: Graph) -> ChatHistory:
    """The history (with cards showing each copy's CURRENT status) + the question still waiting, if any."""
    session = await get_owned_or_404(db, ChatSession, session_id, user)
    await release_expired(db)                       # expired holds show as in stock again
    messages = (await db.scalars(select(ChatMessage).where(ChatMessage.session_id == session.id)
                                 .order_by(ChatMessage.id))).all()

    ids = {i for m in messages for i in m.inventory_item_ids}
    cards: dict[int, ChatItem] = {}
    if ids:
        rows = await db.execute(
            select(InventoryItem.id, Record.artist, Record.title, Record.original_year, Record.release_year,
                   Record.is_reissue, InventoryItem.media_condition, InventoryItem.sleeve_condition,
                   InventoryItem.price, InventoryItem.status)
            .join(Record, Record.id == InventoryItem.record_id).where(InventoryItem.id.in_(ids)))
        for r in rows:
            cards[r.id] = ChatItem(id=r.id, artist=r.artist, title=r.title, year=r.original_year,
                                   pressing=f'{r.release_year} reissue' if r.is_reissue else 'original',
                                   media_condition=r.media_condition, sleeve_condition=r.sleeve_condition,
                                   price=r.price, status=r.status)

    return ChatHistory(
        session=SessionOut.model_validate(session),
        messages=[MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at,
                             items=[cards[i] for i in m.inventory_item_ids if i in cards]) for m in messages],
        pending_confirmation=await pending_confirmation(graph, make_config(session.id, user)),
    )


@router.delete('/sessions/{session_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: int, user: CurrentUser, db: DbSession, graph: Graph) -> Response:
    """Deletes the chat, its history (FK cascade) and the graph's checkpoints for its thread."""
    session = await get_owned_or_404(db, ChatSession, session_id, user)
    await db.delete(session)
    await db.commit()
    await graph.checkpointer.adelete_thread(thread_id(session_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- talking ----------


@router.post('/sessions/{session_id}/messages', response_class=StreamingResponse,
             responses={200: {'content': {'text/event-stream': {}}, 'description': 'SSE stream of the graph run'}})
async def send_message(session_id: int, body: MessageIn, user: CurrentUser, db: DbSession, graph: Graph):
    session = await get_owned_or_404(db, ChatSession, session_id, user)
    config = make_config(session.id, user)
    if await pending_confirmation(graph, config):
        raise HTTPException(status.HTTP_409_CONFLICT, 'Please confirm or cancel the pending action first.')

    if session.title == DEFAULT_TITLE:
        session.title = title_from(body.content)
        await db.commit()
    await save_message(session.id, 'user', body.content)          # saved BEFORE the graph runs
    return _stream(graph, new_turn_input(body.content, user.id), config, session.id)


@router.post('/sessions/{session_id}/confirm', response_class=StreamingResponse,
             responses={200: {'content': {'text/event-stream': {}}, 'description': 'SSE stream of the resumed run'}})
async def confirm(session_id: int, body: ConfirmIn, user: CurrentUser, db: DbSession, graph: Graph):
    """The answer to confirmation_required: resumes the paused graph with Command(resume=...)."""
    session = await get_owned_or_404(db, ChatSession, session_id, user)
    config = make_config(session.id, user)
    if not await pending_confirmation(graph, config):
        raise HTTPException(status.HTTP_409_CONFLICT, 'Nothing is waiting for confirmation.')

    await save_message(session.id, 'user', 'Yes, please.' if body.approved else 'No, thanks.')
    return _stream(graph, Command(resume={'confirmed': body.approved}), config, session.id)
