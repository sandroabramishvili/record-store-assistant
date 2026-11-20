from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

CONDITIONS_SQL = "('P', 'F', 'G', 'G+', 'VG', 'VG+', 'NM', 'M')"


class User(Base):
    __tablename__ = 'users'
    __table_args__ = (
        CheckConstraint("role IN ('customer', 'staff')", name='role_valid'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default='customer', server_default='customer')
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    profile: Mapped['TasteProfile'] = relationship(
        back_populates='user', uselist=False, lazy='raise', cascade='all, delete-orphan', passive_deletes=True,
    )


class TasteProfile(Base):
    """1:1 with User. Created together with the user at registration."""

    __tablename__ = 'taste_profiles'
    __table_args__ = (
        CheckConstraint('budget_cap IS NULL OR budget_cap > 0', name='budget_cap_positive'),
        CheckConstraint(f'min_condition IS NULL OR min_condition IN {CONDITIONS_SQL}', name='min_condition_valid'),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    favorite_genres: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list, server_default='{}')
    disliked_genres: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list, server_default='{}')
    budget_cap: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    min_condition: Mapped[str | None] = mapped_column(String(3))

    user: Mapped[User] = relationship(back_populates='profile', lazy='raise')
