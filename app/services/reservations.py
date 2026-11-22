"""Reservation service: the one service that writes to the DB itself, because atomicity is the point.

6.1 Lazy expiry: reservations last 48 hours. Instead of a background job that wakes up
and releases old ones, every code path that cares about stock first calls
`release_expired`. Nobody can see stale "reserved" data, because anyone who looks
releases the expired ones first.
"""

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InventoryItem, Reservation

HOLD_HOURS = 48


async def release_expired(db: AsyncSession) -> list[int]:
    """Mark active reservations past `expires_at` as `expired` and put their copies back in stock.

    Returns the ids of the copies that were released.

    - Uses the DATABASE clock (`now()`), so app servers with wrong clocks can't disagree.
    - Two UPDATE statements, each atomic. If two requests run this at the same time,
      Postgres locks the rows; the second re-checks `status = 'active'`, finds nothing,
      and releases nothing twice (safe to call any number of times).
    - Commits only if something changed, so it should be called at the START of a
      request, before the caller has made changes of its own.
    """
    result = await db.execute(
        update(Reservation)
        .where(Reservation.status == 'active', Reservation.expires_at <= func.now())
        .values(status='expired')
        .returning(Reservation.inventory_item_id)
    )
    expired_item_ids = list(result.scalars())
    if not expired_item_ids:
        return []

    released = await db.execute(
        update(InventoryItem)
        # Only copies still marked 'reserved': a copy staff already sold stays sold.
        .where(InventoryItem.id.in_(expired_item_ids), InventoryItem.status == 'reserved')
        .values(status='in_stock')
        .returning(InventoryItem.id)
    )
    released_ids = list(released.scalars())
    # Commit now: the release is a fact on its own and must survive even if the
    # caller's own work later fails and rolls back.
    await db.commit()
    return released_ids
