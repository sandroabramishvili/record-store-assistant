"""Wantlist: what the customer is looking for. Matches are found by code (app/services/wantlist.py)."""

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from app.auth.deps import CurrentUser, DbSession
from app.auth.ownership import get_owned_or_404
from app.models import InventoryItem, Record, WantlistItem
from app.schemas.collection import WantlistCreate, WantlistItemOut, WantlistMatch, WantlistOut
from app.services.normalize import normalize_name
from app.repositories.wantlist import refresh_matches

router = APIRouter(prefix='/wantlist', tags=['wantlist'])


async def _with_match(db: DbSession, entries: list[WantlistItem]) -> list[WantlistItemOut]:
    """Attach the matched copy's details (one query for all entries)."""
    ids = {e.matched_item_id for e in entries if e.matched_item_id}
    details = {}
    if ids:
        rows = await db.execute(
            select(InventoryItem.id.label('item_id'), Record.id.label('record_id'), Record.artist, Record.title,
                   InventoryItem.price, InventoryItem.media_condition, InventoryItem.sleeve_condition)
            .join(Record, Record.id == InventoryItem.record_id)
            .where(InventoryItem.id.in_(ids))
        )
        details = {r.item_id: WantlistMatch.model_validate(r._asdict()) for r in rows}
    out = []
    for e in entries:
        item = WantlistItemOut.model_validate(e)
        item.match = details.get(e.matched_item_id)
        out.append(item)
    return out


@router.get('', response_model=WantlistOut)
async def list_wantlist(user: CurrentUser, db: DbSession) -> WantlistOut:
    entries = list((await db.scalars(select(WantlistItem).where(WantlistItem.user_id == user.id))).all())
    await refresh_matches(db, entries)  # stale matches (sold, reserved, repriced) are re-checked now
    entries.sort(key=lambda e: (e.matched_item_id is None, -e.created_at.timestamp()))  # matches first, then newest
    items = await _with_match(db, entries)
    return WantlistOut(items=items, match_count=sum(i.match is not None for i in items))


@router.post('', response_model=WantlistItemOut, status_code=status.HTTP_201_CREATED)
async def add_to_wantlist(data: WantlistCreate, user: CurrentUser, db: DbSession, response: Response) -> WantlistItemOut:
    """Same artist + title already wanted → 200 with the existing entry (like /collection).

    A new entry is checked against current stock right away: if the store already
    has it, the response already contains the match.
    """
    artist_norm = normalize_name(data.artist)
    title_norm = normalize_name(data.title) if data.title else None

    existing = await db.scalar(select(WantlistItem).where(
        WantlistItem.user_id == user.id,
        WantlistItem.artist_norm == artist_norm,
        WantlistItem.title_norm.is_(None) if title_norm is None else WantlistItem.title_norm == title_norm,
    ))
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        entry = existing
    else:
        entry = WantlistItem(user_id=user.id, **data.model_dump(), artist_norm=artist_norm, title_norm=title_norm)
        db.add(entry)
        await db.commit()

    await refresh_matches(db, [entry])
    return (await _with_match(db, [entry]))[0]


@router.delete('/{item_id}', status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_wantlist(item_id: int, user: CurrentUser, db: DbSession) -> Response:
    item = await get_owned_or_404(db, WantlistItem, item_id, user)
    await db.delete(item)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
