"""Wantlist: what the customer is looking for. Matches are set by code when stock arrives (Phase 6.4)."""

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from app.auth.deps import CurrentUser, DbSession
from app.auth.ownership import get_owned_or_404
from app.models import WantlistItem
from app.schemas.collection import WantlistCreate, WantlistItemOut, WantlistOut
from app.services.normalize import normalize_name

router = APIRouter(prefix='/wantlist', tags=['wantlist'])


@router.get('', response_model=WantlistOut)
async def list_wantlist(user: CurrentUser, db: DbSession) -> WantlistOut:
    items = (await db.scalars(
        select(WantlistItem)
        .where(WantlistItem.user_id == user.id)
        # matches first, then newest
        .order_by(WantlistItem.matched_item_id.is_(None), WantlistItem.created_at.desc())
    )).all()
    return WantlistOut(
        items=[WantlistItemOut.model_validate(i) for i in items],
        match_count=sum(i.matched_item_id is not None for i in items),
    )


@router.post('', response_model=WantlistItemOut, status_code=status.HTTP_201_CREATED)
async def add_to_wantlist(data: WantlistCreate, user: CurrentUser, db: DbSession, response: Response) -> WantlistItem:
    """Same artist + title already wanted → 200 with the existing entry (like /collection)."""
    artist_norm = normalize_name(data.artist)
    title_norm = normalize_name(data.title) if data.title else None

    existing = await db.scalar(select(WantlistItem).where(
        WantlistItem.user_id == user.id,
        WantlistItem.artist_norm == artist_norm,
        WantlistItem.title_norm.is_(None) if title_norm is None else WantlistItem.title_norm == title_norm,
    ))
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return existing

    item = WantlistItem(user_id=user.id, **data.model_dump(), artist_norm=artist_norm, title_norm=title_norm)
    db.add(item)
    await db.commit()
    # TODO Phase 6.4: check current stock for an immediate match.
    return item


@router.delete('/{item_id}', status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_wantlist(item_id: int, user: CurrentUser, db: DbSession) -> Response:
    item = await get_owned_or_404(db, WantlistItem, item_id, user)
    await db.delete(item)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
