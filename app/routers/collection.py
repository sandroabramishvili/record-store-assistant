"""My Collection: records the customer already owns (so the assistant won't recommend them)."""

from collections import Counter

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.deps import CurrentUser, DbSession
from app.auth.ownership import get_owned_or_404
from app.models import CollectionItem, Record
from app.schemas.collection import CollectionCreate, CollectionItemOut, CollectionOut, CollectionStats
from app.services.normalize import normalize_name

router = APIRouter(prefix='/collection', tags=['collection'])


@router.get('', response_model=CollectionOut)
async def list_collection(user: CurrentUser, db: DbSession) -> CollectionOut:
    rows = (await db.execute(
        select(CollectionItem, Record.genres, Record.original_year)
        .outerjoin(Record, Record.id == CollectionItem.record_id)
        .where(CollectionItem.user_id == user.id)
        .order_by(CollectionItem.artist_norm, CollectionItem.title_norm)
    )).all()

    by_genre: Counter[str] = Counter()
    by_decade: Counter[str] = Counter()
    in_catalog = 0
    for item, genres, year in rows:
        if item.record_id is not None:
            in_catalog += 1
            by_genre.update(genres)
            by_decade[f'{year // 10 * 10}s'] += 1

    return CollectionOut(
        items=[CollectionItemOut.model_validate(item) for item, _, _ in rows],
        stats=CollectionStats(
            total=len(rows),
            in_catalog=in_catalog,
            by_genre=dict(by_genre.most_common()),
            by_decade=dict(sorted(by_decade.items())),
        ),
    )


@router.post('', response_model=CollectionItemOut, status_code=status.HTTP_201_CREATED)
async def add_to_collection(
    data: CollectionCreate, user: CurrentUser, db: DbSession, response: Response
) -> CollectionItem:
    """Add by `record_id`, or by free text (auto-linked to the catalog if the names match).

    Already in the collection → 200 with the existing entry (adding twice is harmless).
    """
    if data.record_id is not None:
        record = await db.get(Record, data.record_id)
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, 'Record not found')
        artist, title = record.artist, record.title
    else:
        artist, title = data.artist, data.title
        record = None

    artist_norm, title_norm = normalize_name(artist), normalize_name(title)

    existing = await db.scalar(select(CollectionItem).where(
        CollectionItem.user_id == user.id,
        CollectionItem.artist_norm == artist_norm,
        CollectionItem.title_norm == title_norm,
    ))
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing

    if record is None:
        # Free text that matches a catalog release → link it (genre/decade stats, ownership by id).
        record = await db.scalar(
            select(Record).where(Record.artist_norm == artist_norm, Record.title_norm == title_norm)
            .order_by(Record.id).limit(1)
        )
        if record is not None:
            artist, title = record.artist, record.title  # use the catalog's spelling

    item = CollectionItem(
        user_id=user.id,
        record_id=record.id if record else None,
        artist=artist, title=title, artist_norm=artist_norm, title_norm=title_norm,
    )
    db.add(item)
    try:
        await db.commit()
    except IntegrityError:
        # Same album added twice at the same instant: the unique constraint decides.
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, 'Already in your collection')
    return item


@router.delete('/{item_id}', status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_collection(item_id: int, user: CurrentUser, db: DbSession) -> Response:
    item = await get_owned_or_404(db, CollectionItem, item_id, user)
    await db.delete(item)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
