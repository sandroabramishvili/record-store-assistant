"""Wantlist matching, DB side: load entries and copies, apply the pure rules, store `matched_item_id`.

- `match_new_copy`: staff added/edited a copy → give it to entries that have no valid match (eager).
- `refresh_matches`: an entry was added / the wantlist is opened → re-check against current
  stock (lazy). A match is only valid while its copy is in stock and still fits, so stale
  matches (sold, reserved, repriced) are fixed whenever someone looks, like reservation expiry.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import InventoryItem, Record, WantlistItem
from app.services.reservations import release_expired
from app.services.wantlist import best_copy, entry_matches, find_matches

_COPY_COLUMNS = (
    InventoryItem.id, InventoryItem.price, InventoryItem.media_condition, InventoryItem.status,
    Record.artist_norm, Record.title_norm,
)
_ENTRY_FIELDS = ('id', 'artist_norm', 'title_norm', 'max_price', 'min_condition', 'matched_item_id')


def _entry_dict(e: WantlistItem) -> dict:
    return {f: getattr(e, f) for f in _ENTRY_FIELDS}


async def _in_stock_copies(db: AsyncSession, artist_norms: set[str]) -> list[dict]:
    rows = await db.execute(
        select(*_COPY_COLUMNS)
        .join(Record, Record.id == InventoryItem.record_id)
        .where(InventoryItem.status == 'in_stock', Record.artist_norm.in_(artist_norms))
    )
    return [r._asdict() for r in rows]


async def match_new_copy(db: AsyncSession, item_id: int) -> list[int]:
    """Give this copy to every entry (any customer) that has no valid match yet.

    Entries whose current match is still valid keep it (no jumping around).
    Returns the ids of the entries now matched to this copy. Commits if anything changed.
    """
    row = (await db.execute(
        select(*_COPY_COLUMNS).join(Record, Record.id == InventoryItem.record_id).where(InventoryItem.id == item_id)
    )).one_or_none()
    if row is None or row.status != 'in_stock':
        return []
    copy = row._asdict()

    entries = (await db.scalars(
        select(WantlistItem).where(WantlistItem.artist_norm == copy['artist_norm'])
    )).all()
    if not entries:
        return []

    # Entries whose match is another copy that is still in stock and still fits keep it.
    others = {c['id']: c for c in await _in_stock_copies(db, {copy['artist_norm']}) if c['id'] != item_id}
    open_entries = [
        e for e in entries
        if not (e.matched_item_id in others and entry_matches(_entry_dict(e), others[e.matched_item_id]))
    ]

    matched = set(find_matches(copy, (_entry_dict(e) for e in open_entries)))
    changed = False
    for e in open_entries:
        new = item_id if e.id in matched else None   # None also clears a stale match
        if e.matched_item_id != new:
            e.matched_item_id = new
            changed = True
    if changed:
        await db.commit()
    return sorted(matched)


async def refresh_matches(db: AsyncSession, entries: Sequence[WantlistItem]) -> int:
    """Keep a still-valid match, otherwise pick the best in-stock copy, otherwise clear.

    Returns how many entries changed. Commits if anything changed.
    """
    if not entries:
        return 0
    await release_expired(db)  # expired holds are back on sale and can be matched
    copies = await _in_stock_copies(db, {e.artist_norm for e in entries})
    by_id = {c['id']: c for c in copies}

    changed = 0
    for e in entries:
        entry = _entry_dict(e)
        current = by_id.get(e.matched_item_id)
        if current is not None and entry_matches(entry, current):
            continue
        new = best_copy(entry, copies)
        if e.matched_item_id != new:
            e.matched_item_id = new
            changed += 1
    if changed:
        await db.commit()
    return changed
