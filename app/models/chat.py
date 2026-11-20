from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ChatSession(Base):
    """One chat = one LangGraph thread (thread_id = f'chat-{id}')."""

    __tablename__ = 'chat_sessions'

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(255), default='New chat', server_default='New chat')
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    messages: Mapped[list['ChatMessage']] = relationship(
        back_populates='session', lazy='raise', cascade='all, delete-orphan', passive_deletes=True,
        order_by='ChatMessage.id',
    )


class ChatMessage(Base):
    """For showing history in the UI quickly. The graph's real state lives in the checkpointer tables."""

    __tablename__ = 'chat_messages'
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name='role_valid'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('chat_sessions.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    inventory_item_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list, server_default='{}')
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)

    session: Mapped[ChatSession] = relationship(back_populates='messages', lazy='raise')
