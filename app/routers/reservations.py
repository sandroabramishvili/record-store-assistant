"""Reservations. Every change goes through app/services/reservations.py.

Service errors (ReservationError) are turned into HTTP responses by the
exception handler in main.py, so the endpoints contain no error mapping.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, select

from app.auth.deps import CurrentUser, DbSession, StaffUser
from app.auth.ownership import get_owned_or_404
from app.models import InventoryItem, Record, Reservation
from app.schemas.common import Page
from app.schemas.reservation import ReservationCreate, ReservationOut, ReservationStatus
from app.services.reservations import (
    cancel_reservation,
    complete_reservation,
    release_expired,
    reserve_item,
)

router = APIRouter(prefix='/reservations', tags=['reservations'])

# One SELECT shape for every response: reservation + copy + release.
_OUT_COLUMNS = (
    Reservation.id, Reservation.user_id, Reservation.status, Reservation.expires_at, Reservation.created_at,
    Reservation.inventory_item_id, InventoryItem.price, InventoryItem.media_condition, InventoryItem.sleeve_condition,
    Record.id.label('record_id'), Record.artist, Record.title,
)


def _out_query():
    return (
        select(*_OUT_COLUMNS)
        .join(InventoryItem, InventoryItem.id == Reservation.inventory_item_id)
        .join(Record, Record.id == InventoryItem.record_id)
    )


async def _load_out(db: DbSession, reservation_id: int) -> ReservationOut:
    row = (await db.execute(_out_query().where(Reservation.id == reservation_id))).one()
    return ReservationOut.model_validate(row._asdict())


@router.get('', response_model=Page[ReservationOut])
async def list_reservations(
    user: CurrentUser,
    db: DbSession,
    status_filter: Annotated[ReservationStatus | None, Query(alias='status')] = None,
    user_id: Annotated[int | None, Query(description='Staff only: one customer')] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> Page[ReservationOut]:
    """Customer: own reservations. Staff: everyone's, optionally filtered by customer."""
    await release_expired(db)  # so nothing is listed as active after its 48 h

    filters = []
    if user.role != 'staff':
        filters.append(Reservation.user_id == user.id)   # a customer's user_id param is ignored
    elif user_id is not None:
        filters.append(Reservation.user_id == user_id)
    if status_filter is not None:
        filters.append(Reservation.status == status_filter)

    total = await db.scalar(select(func.count()).select_from(Reservation).where(*filters))
    rows = await db.execute(
        _out_query().where(*filters)
        .order_by(case((Reservation.status == 'active', 0), else_=1), Reservation.created_at.desc())
        .offset((page - 1) * size).limit(size)
    )
    items = [ReservationOut.model_validate(r._asdict()) for r in rows]
    return Page(items=items, total=total, page=page, size=size)


@router.post('', response_model=ReservationOut, status_code=status.HTTP_201_CREATED)
async def create_reservation(data: ReservationCreate, user: CurrentUser, db: DbSession) -> ReservationOut:
    """The catalog's "Reserve" button. Same service function as the chat's confirmation."""
    reservation = await reserve_item(db, user.id, data.inventory_item_id)
    return await _load_out(db, reservation.id)


@router.delete('/{reservation_id}', response_model=ReservationOut)
async def cancel(reservation_id: int, user: CurrentUser, db: DbSession) -> ReservationOut:
    """Owner or staff. The copy goes back on sale. Returns the cancelled reservation."""
    await get_owned_or_404(db, Reservation, reservation_id, user, allow_staff=True)
    await cancel_reservation(db, reservation_id)
    return await _load_out(db, reservation_id)


@router.post('/{reservation_id}/complete', response_model=ReservationOut)
async def complete(reservation_id: int, staff: StaffUser, db: DbSession) -> ReservationOut:
    """Staff: the customer picked it up and paid → the copy is sold."""
    if await db.get(Reservation, reservation_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Not found')
    await complete_reservation(db, reservation_id)
    return await _load_out(db, reservation_id)
