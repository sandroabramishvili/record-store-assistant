"""Physical copies. Staff only (the whole router)."""

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth.deps import DbSession, require_staff
from app.models import InventoryItem, Record
from app.repositories.inventory import has_active_reservation
from app.schemas.catalog import InventoryCreate, InventoryIn, InventoryOut, StatusUpdate
from app.repositories.wantlist import match_new_copy

# `dependencies=` on the router applies require_staff to EVERY endpoint below,
# so a new endpoint can't accidentally be left open.
router = APIRouter(prefix='/inventory', tags=['inventory'], dependencies=[Depends(require_staff)])


async def _get_item_or_404(db: DbSession, item_id: int) -> InventoryItem:
    item = await db.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Copy not found')
    return item


@router.post('', response_model=InventoryOut, status_code=status.HTTP_201_CREATED)
async def create_item(data: InventoryCreate, db: DbSession) -> InventoryItem:
    if await db.get(Record, data.record_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Record not found')
    item = InventoryItem(**data.model_dump(), status='in_stock')
    db.add(item)
    await db.commit()
    await match_new_copy(db, item.id)  # wantlists waiting for this record get their match
    return item


@router.put('/{item_id}', response_model=InventoryOut)
async def update_item(item_id: int, data: InventoryIn, db: DbSession) -> InventoryItem:
    """Edit condition / price / notes. The release and the status can't be changed here."""
    item = await _get_item_or_404(db, item_id)
    for field, value in data.model_dump().items():
        setattr(item, field, value)
    await db.commit()
    await match_new_copy(db, item.id)  # a price cut or regrade can create (or break) a match
    return item


@router.patch('/{item_id}', response_model=InventoryOut)
async def change_status(item_id: int, data: StatusUpdate, db: DbSession) -> InventoryItem:
    """Mark a copy `sold` (sold over the counter) or back `in_stock`.

    A copy with an active reservation is handled through /reservations
    (complete or cancel), so its reservation and status stay consistent.
    """
    item = await _get_item_or_404(db, item_id)
    if await has_active_reservation(db, item_id=item_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, 'Copy has an active reservation; complete or cancel it via /reservations'
        )
    item.status = data.status
    await db.commit()
    if item.status == 'in_stock':
        await match_new_copy(db, item.id)  # back on the shelf → may fulfil a wantlist
    return item


@router.delete('/{item_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: int, db: DbSession) -> Response:
    item = await _get_item_or_404(db, item_id)
    if await has_active_reservation(db, item_id=item_id):
        raise HTTPException(status.HTTP_409_CONFLICT, 'Copy has an active reservation')
    await db.delete(item)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
