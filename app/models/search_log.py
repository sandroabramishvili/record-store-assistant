from datetime import datetime

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SearchLog(Base):
    """One row per assistant search. Zero-result rows show unmet demand to staff."""

    __tablename__ = 'search_logs'

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    intent: Mapped[str] = mapped_column(String(30))
    constraints: Mapped[dict] = mapped_column(JSONB, default=dict, server_default='{}')
    results_count: Mapped[int]
    relax_attempts: Mapped[int] = mapped_column(default=0, server_default='0')
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
