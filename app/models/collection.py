from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.user import CONDITIONS_SQL


class CollectionItem(Base):
    """A record the customer already owns. May or may not exist in the store's catalog."""

    __tablename__ = 'collection_items'
    __table_args__ = (
        # The same album can't be added twice (compared by normalized names)
        UniqueConstraint('user_id', 'artist_norm', 'title_norm'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    record_id: Mapped[int | None] = mapped_column(ForeignKey('records.id', ondelete='SET NULL'))
    artist: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    artist_norm: Mapped[str] = mapped_column(String(255))
    title_norm: Mapped[str] = mapped_column(String(255))
    added_at: Mapped[datetime] = mapped_column(server_default=func.now())


class WantlistItem(Base):
    """Something the customer is looking for. Matched by code when staff add new stock."""

    __tablename__ = 'wantlist_items'
    __table_args__ = (
        CheckConstraint('max_price IS NULL OR max_price > 0', name='max_price_positive'),
        CheckConstraint(f'min_condition IS NULL OR min_condition IN {CONDITIONS_SQL}', name='min_condition_valid'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    artist: Mapped[str] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255))          # NULL = anything by this artist
    artist_norm: Mapped[str] = mapped_column(String(255), index=True)
    title_norm: Mapped[str | None] = mapped_column(String(255))
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    min_condition: Mapped[str | None] = mapped_column(String(3))
    matched_item_id: Mapped[int | None] = mapped_column(ForeignKey('inventory_items.id', ondelete='SET NULL'))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
