"""Catalog of releases. Everyone logged in can browse; only staff can change it.

Every change to a release also updates its vector (one per release, id `record-{id}`).
The DB is the source of truth: it is saved first; if embedding then fails (e.g. Gemini
is down), the request still succeeds, the error is logged, and the vectors can be
rebuilt with `python -m scripts.seed_catalog --vectors-only`.
"""

import logging
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import exists, func, or_, select

from app.ai.indexing import delete_record_vector, index_records
from app.ai.vectorstores import RecordStore
from app.auth.deps import CurrentUser, DbSession, StaffUser
from app.models import InventoryItem, Record
from app.repositories.inventory import has_active_reservation
from app.schemas.catalog import Format, InventoryOut, RecordDetail, RecordIn, RecordOut, RecordSummary
from app.schemas.common import Page
from app.services.conditions import CONDITION_ORDER, Condition, condition_rank
from app.services.normalize import normalize_name
from app.services.reservations import release_expired

router = APIRouter(prefix='/records', tags=['catalog'])
logger = logging.getLogger(__name__)


async def _reindex(store, record: Record) -> None:
    try:
        await index_records(store, [record])
    except Exception:
        logger.exception('Could not index record %s; run scripts.seed_catalog --vectors-only', record.id)


def _apply(record: Record, data: RecordIn) -> None:
    """Copy validated fields onto the model and compute the derived ones."""
    for field, value in data.model_dump().items():
        setattr(record, field, value)
    record.artist_norm = normalize_name(data.artist)
    record.title_norm = normalize_name(data.title)
    record.is_reissue = data.release_year != data.original_year  # computed, never sent


async def _get_record_or_404(db: DbSession, record_id: int) -> Record:
    record = await db.get(Record, record_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Record not found')
    return record


@router.get('', response_model=Page[RecordSummary])
async def list_records(
    user: CurrentUser,
    db: DbSession,
    genre: str | None = None,
    artist: str | None = None,
    decade: Annotated[int | None, Query(ge=1900, le=2030, multiple_of=10, description='e.g. 1970')] = None,
    format: Format | None = None,
    in_stock: Annotated[bool | None, Query(description='true: has an available copy; false: none')] = None,
    max_price: Annotated[Decimal | None, Query(gt=0)] = None,
    min_condition: Condition | None = None,
    search: Annotated[str | None, Query(max_length=100, description='Artist, title, label or catalog no.')] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
) -> Page[RecordSummary]:
    await release_expired(db)  # expired reservations count as in stock again
    filters = []
    if genre:
        g = genre.strip().lower()
        filters.append(or_(Record.genres.any(g), Record.styles.any(g)))
    if artist:
        filters.append(Record.artist_norm.contains(normalize_name(artist), autoescape=True))
    if decade is not None:
        filters.append(Record.original_year.between(decade, decade + 9))
    if format:
        filters.append(Record.format == format)
    if search:
        filters.append(or_(*(
            col.icontains(search.strip(), autoescape=True)
            for col in (Record.artist, Record.title, Record.label, Record.catalog_number)
        )))

    # Copy-level filters: price and condition only make sense for copies you can buy,
    # so they all describe ONE in-stock copy that satisfies every condition at once.
    copy_filters = [InventoryItem.record_id == Record.id, InventoryItem.status == 'in_stock']
    if max_price is not None:
        copy_filters.append(InventoryItem.price <= max_price)
    if min_condition is not None:
        copy_filters.append(InventoryItem.media_condition.in_(CONDITION_ORDER[condition_rank(min_condition):]))
    has_matching_copy = exists().where(*copy_filters)

    if in_stock is False:
        if max_price is not None or min_condition is not None:
            raise HTTPException(422, 'max_price and min_condition only apply to copies in stock')
        filters.append(~has_matching_copy)
    elif in_stock or max_price is not None or min_condition is not None:
        filters.append(has_matching_copy)

    total = await db.scalar(select(func.count()).select_from(Record).where(*filters))

    # Per release: how many in-stock copies and the cheapest one (for the list view).
    stock = (
        select(
            InventoryItem.record_id,
            func.count().label('available_copies'),
            func.min(InventoryItem.price).label('lowest_price'),
        )
        .where(InventoryItem.status == 'in_stock')
        .group_by(InventoryItem.record_id)
        .subquery()
    )
    rows = await db.execute(
        select(Record, func.coalesce(stock.c.available_copies, 0), stock.c.lowest_price)
        .outerjoin(stock, stock.c.record_id == Record.id)
        .where(*filters)
        .order_by(Record.artist_norm, Record.title_norm, Record.id)  # stable order → stable pages
        .offset((page - 1) * size)
        .limit(size)
    )
    items = [
        RecordSummary(**RecordOut.model_validate(rec).model_dump(), available_copies=count, lowest_price=low)
        for rec, count, low in rows
    ]
    return Page(items=items, total=total, page=page, size=size)


@router.get('/{record_id}', response_model=RecordDetail)
async def get_record(record_id: int, user: CurrentUser, db: DbSession) -> RecordDetail:
    """Release + tracklist + copies. Customers see copies in stock; staff see every copy."""
    await release_expired(db)
    record = await _get_record_or_404(db, record_id)
    query = select(InventoryItem).where(InventoryItem.record_id == record_id).order_by(InventoryItem.price)
    if user.role != 'staff':
        query = query.where(InventoryItem.status == 'in_stock')
    copies = (await db.scalars(query)).all()
    return RecordDetail.model_validate(
        {**RecordOut.model_validate(record).model_dump(),
         'description': record.description, 'tracklist': record.tracklist,
         'created_at': record.created_at,
         'copies': [InventoryOut.model_validate(c) for c in copies]}
    )


@router.post('', response_model=RecordOut, status_code=status.HTTP_201_CREATED)
async def create_record(data: RecordIn, staff: StaffUser, db: DbSession, store: RecordStore) -> Record:
    record = Record()
    _apply(record, data)
    db.add(record)
    await db.commit()
    await _reindex(store, record)
    return record


@router.put('/{record_id}', response_model=RecordOut)
async def update_record(record_id: int, data: RecordIn, staff: StaffUser, db: DbSession, store: RecordStore) -> Record:
    record = await _get_record_or_404(db, record_id)
    _apply(record, data)
    await db.commit()
    await _reindex(store, record)  # same id → the old vector is overwritten
    return record


@router.delete('/{record_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_record(record_id: int, staff: StaffUser, db: DbSession, store: RecordStore) -> Response:
    """Deletes the release and all its copies. 409 while any copy is actively reserved."""
    record = await _get_record_or_404(db, record_id)
    if await has_active_reservation(db, record_id=record_id):
        raise HTTPException(status.HTTP_409_CONFLICT, 'A copy of this release is reserved; cancel or complete it first')
    await db.delete(record)  # copies cascade in Postgres; collection links become NULL
    await db.commit()
    try:
        await delete_record_vector(store, record_id)
    except Exception:
        # A leftover vector is harmless: search joins back to SQL, where the record is gone.
        logger.exception('Could not delete vector of record %s', record_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
