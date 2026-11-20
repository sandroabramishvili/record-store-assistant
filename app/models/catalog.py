from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.user import CONDITIONS_SQL


class Record(Base):
    """A release (one specific pressing of an album). One vector per release."""

    __tablename__ = 'records'
    __table_args__ = (
        CheckConstraint("format IN ('LP', 'EP', '7\"', '10\"', '12\"')", name='format_valid'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    artist: Mapped[str] = mapped_column(String(255))
    artist_norm: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255))
    title_norm: Mapped[str] = mapped_column(String(255), index=True)
    label: Mapped[str] = mapped_column(String(150))
    catalog_number: Mapped[str | None] = mapped_column(String(50))
    release_year: Mapped[int]                     # year of this pressing
    original_year: Mapped[int]                    # year the album first came out
    country: Mapped[str | None] = mapped_column(String(50))
    format: Mapped[str] = mapped_column(String(10))
    is_reissue: Mapped[bool]                      # computed by code: release_year != original_year
    genres: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list, server_default='{}')
    styles: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list, server_default='{}')
    description: Mapped[str] = mapped_column(Text)
    tracklist: Mapped[list[dict]] = mapped_column(JSONB, default=list, server_default='[]')
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    copies: Mapped[list['InventoryItem']] = relationship(
        back_populates='record', lazy='raise', cascade='all, delete-orphan', passive_deletes=True,
    )


class InventoryItem(Base):
    """One physical copy of a release, with its own condition, price and status."""

    __tablename__ = 'inventory_items'
    __table_args__ = (
        CheckConstraint('price > 0', name='price_positive'),
        CheckConstraint("status IN ('in_stock', 'reserved', 'sold')", name='status_valid'),
        CheckConstraint(f'media_condition IN {CONDITIONS_SQL}', name='media_condition_valid'),
        CheckConstraint(f'sleeve_condition IN {CONDITIONS_SQL}', name='sleeve_condition_valid'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey('records.id', ondelete='CASCADE'), index=True)
    media_condition: Mapped[str] = mapped_column(String(3))
    sleeve_condition: Mapped[str] = mapped_column(String(3))
    price: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    status: Mapped[str] = mapped_column(String(20), default='in_stock', server_default='in_stock', index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(server_default=func.now())

    record: Mapped[Record] = relationship(back_populates='copies', lazy='raise')
