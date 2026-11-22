"""Reservation service: the one service that writes to the DB itself, because atomicity is the point.

6.1 Lazy expiry: reservations last 48 hours. Instead of a background job that wakes up
and releases old ones, every code path that cares about stock first calls
`release_expired`. Nobody can see stale "reserved" data, because anyone who looks
releases the expired ones first.
"""

from datetime import timedelta

from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InventoryItem, Reservation, User
# Constants and errors live in a pure module (the graph imports them without the DB);
# re-exported here so existing imports keep working.
from app.services.reservation_rules import (  # noqa: F401
    HOLD_HOURS,
    MAX_ACTIVE,
    ItemNotFound,
    ReservationConflict,
    ReservationError,
    ReservationLimitReached,
    ReservationNotActive,
)


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


# ---------- 6.2 atomic reservation ----------


async def reserve_item(db: AsyncSession, user_id: int, item_id: int) -> Reservation:
    """Reserve one copy for 48 hours. THE only way a reservation is created
    (chat confirmation and any "Reserve" button both call this).

    Raises ReservationLimitReached, ReservationConflict or ItemNotFound;
    on any error nothing is written.
    """
    await release_expired(db)  # an expired hold must not block this copy or count toward the limit

    try:
        # 1. Serialize THIS customer's reservations: lock their user row until commit.
        #    Without it, two simultaneous requests could both count 2 active and both
        #    succeed → 4. Other customers aren't blocked (different rows).
        await db.execute(select(User.id).where(User.id == user_id).with_for_update())

        # 2. The limit.
        active = await db.scalar(
            select(func.count()).select_from(Reservation)
            .where(Reservation.user_id == user_id, Reservation.status == 'active')
        )
        if active >= MAX_ACTIVE:
            raise ReservationLimitReached()

        # 3. Check-and-change in ONE statement: succeeds only if the copy is in stock
        #    at this exact moment. Of two simultaneous requests, Postgres lets one win.
        taken = await db.scalar(
            update(InventoryItem)
            .where(InventoryItem.id == item_id, InventoryItem.status == 'in_stock')
            .values(status='reserved')
            .returning(InventoryItem.id)
        )
        if taken is None:
            exists = await db.scalar(select(InventoryItem.id).where(InventoryItem.id == item_id))
            raise ReservationConflict() if exists else ItemNotFound()

        # 4. The reservation row. Deadline from the DATABASE clock, like release_expired.
        reservation = await db.scalar(
            insert(Reservation)
            .values(
                user_id=user_id,
                inventory_item_id=item_id,
                status='active',
                expires_at=func.now() + timedelta(hours=HOLD_HOURS),
            )
            .returning(Reservation)
        )
        await db.commit()
        return reservation

    except IntegrityError:
        # Safety net: the partial unique index allows one active reservation per copy.
        # Reaching this means the steps above have a bug, but the data stays correct.
        await db.rollback()
        raise ReservationConflict() from None
    except Exception:
        await db.rollback()  # undo the status change and release the lock
        raise


# ---------- 6.3 ending a reservation ----------


async def _finish(db: AsyncSession, reservation_id: int, new_status: str, copy_status: str) -> Reservation:
    """active → `new_status`, and its copy reserved → `copy_status`, atomically.

    Same pattern as reserve_item: the UPDATE only succeeds while the reservation is
    still active, so a cancel and a complete racing each other can't both win.
    """
    await release_expired(db)  # an expired hold can't be completed or cancelled any more
    try:
        reservation = await db.scalar(
            update(Reservation)
            .where(Reservation.id == reservation_id, Reservation.status == 'active')
            .values(status=new_status)
            .returning(Reservation)
        )
        if reservation is None:
            current = await db.scalar(select(Reservation.status).where(Reservation.id == reservation_id))
            raise ReservationNotActive(current or 'gone')
        await db.execute(
            update(InventoryItem)
            .where(InventoryItem.id == reservation.inventory_item_id, InventoryItem.status == 'reserved')
            .values(status=copy_status)
        )
        await db.commit()
        return reservation
    except Exception:
        await db.rollback()
        raise


async def cancel_reservation(db: AsyncSession, reservation_id: int) -> Reservation:
    """Customer or staff cancels → copy back on sale."""
    return await _finish(db, reservation_id, 'cancelled', 'in_stock')


async def complete_reservation(db: AsyncSession, reservation_id: int) -> Reservation:
    """Customer picked it up and paid (staff action) → copy sold."""
    return await _finish(db, reservation_id, 'completed', 'sold')
