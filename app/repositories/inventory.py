"""Small reusable queries about copies, shared by several routers."""

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InventoryItem, Reservation


async def has_active_reservation(
    db: AsyncSession, *, item_id: int | None = None, record_id: int | None = None
) -> bool:
    """True if the copy (`item_id`) or any copy of the release (`record_id`) is actively reserved."""
    query = select(Reservation.id).where(Reservation.status == 'active')
    if item_id is not None:
        query = query.where(Reservation.inventory_item_id == item_id)
    elif record_id is not None:
        query = query.join(InventoryItem, InventoryItem.id == Reservation.inventory_item_id).where(
            InventoryItem.record_id == record_id
        )
    else:
        raise ValueError('Pass item_id or record_id')
    # TODO Phase 6.1: call release_expired(db) first, so an expired reservation doesn't block.
    return bool(await db.scalar(select(exists(query))))
