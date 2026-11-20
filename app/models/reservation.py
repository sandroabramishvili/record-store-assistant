from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Reservation(Base):
    __tablename__ = 'reservations'
    __table_args__ = (
        CheckConstraint("status IN ('active', 'completed', 'cancelled', 'expired')", name='status_valid'),
        # Safety net at the database level: a copy can have at most ONE active reservation.
        # The atomic UPDATE in services/reservations.py is the main guard; this index
        # makes a double reservation impossible even if that code has a bug.
        Index(
            'uq_reservations_one_active_per_item',
            'inventory_item_id',
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey('inventory_items.id', ondelete='CASCADE'))
    status: Mapped[str] = mapped_column(String(20), default='active', server_default='active', index=True)
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
