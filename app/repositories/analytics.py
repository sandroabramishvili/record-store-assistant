"""Staff analytics (spec 7.9): every number is one SQL aggregation (GROUP BY / COUNT), never
rows counted in Python.

Sources:
- search_logs    one row per assistant SEARCH, with the constraints the customer ASKED for
                 (request_constraints, never the relaxed ones) and how many copies came back.
- chat_messages  `intent` on the first assistant reply of every turn (Phase 15), so EVERY
                 request is counted, also off-topic ones that never search.
- reservations / wantlist_items  as they are.

Every query takes `since` (a SQL expression: now() - N days), so the windows use the DB clock.
"""

from sqlalchemy import ColumnElement, String, func, select, true
from sqlalchemy.dialects.postgresql import ARRAY, aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatMessage, Reservation, SearchLog, WantlistItem
from app.services.reservations import release_expired

SEARCH = 'find_record'


def first_written(expr: ColumnElement, *order_by: ColumnElement) -> ColumnElement:
    """Aggregate: the value from the group's earliest row, `(array_agg(expr ORDER BY …))[1]`."""
    return func.array_agg(aggregate_order_by(expr, *order_by), type_=ARRAY(String))[1]


def _is_search(since: ColumnElement) -> list[ColumnElement]:
    return [SearchLog.intent == SEARCH, SearchLog.created_at > since]


async def count_searches(db: AsyncSession, since: ColumnElement, *, zero_results: bool = False) -> int:
    where = _is_search(since) + ([SearchLog.results_count == 0] if zero_results else [])
    return await db.scalar(select(func.count()).select_from(SearchLog).where(*where))


async def top_values(db: AsyncSession, key: str, since: ColumnElement, limit: int,
                     *, zero_results: bool = False) -> list[dict]:
    """Most asked-for values of one list field of the constraints (genres / artists / titles).

    One search for ["jazz", "funk"] counts once for each. Grouped ignoring case, spaces and
    punctuation ("Radiohead" = "radiohead ", "hip hop" = "hip-hop"); shown as first written
    (deterministic, unlike mode()/min(), whose ties and case order depend on the collation).
    """
    field = SearchLog.constraints[key]
    value = func.jsonb_array_elements_text(field).table_valued('value').lateral(f'{key}_value')
    # Same key as services.normalize.normalize_tag, in SQL: case, spaces and punctuation don't
    # count, so "hip hop" and "hip-hop" are one row (they were two in a screenshot).
    name = func.regexp_replace(func.lower(value.c.value), r'[[:space:][:punct:]]+', '', 'g')
    where = _is_search(since) + [func.jsonb_typeof(field) == 'array']   # skip missing / null lists
    if zero_results:
        where.append(SearchLog.results_count == 0)
    rows = await db.execute(
        select(first_written(func.trim(value.c.value), SearchLog.created_at, SearchLog.id).label('name'),
               func.count().label('count'))
        .select_from(SearchLog).join(value, true())
        .where(*where)
        .group_by(name)
        .order_by(func.count().desc(), name)
        .limit(limit)
    )
    return [{'name': r.name, 'count': r.count} for r in rows]


async def top_decades(db: AsyncSession, since: ColumnElement, limit: int) -> list[dict]:
    decade = SearchLog.constraints['decade'].astext
    rows = await db.execute(
        select(decade.label('decade'), func.count().label('count'))
        .where(*_is_search(since), decade.is_not(None))
        .group_by(decade).order_by(func.count().desc(), decade).limit(limit)
    )
    return [{'name': f'{int(r.decade)}s', 'count': r.count} for r in rows]


async def recent_zero_results(db: AsyncSession, since: ColumnElement, limit: int) -> list[dict]:
    """The latest searches that found nothing, as asked (for "what exactly did they want?")."""
    rows = await db.execute(
        select(SearchLog.created_at, SearchLog.constraints, SearchLog.relax_attempts)
        .where(*_is_search(since), SearchLog.results_count == 0)
        .order_by(SearchLog.created_at.desc(), SearchLog.id.desc()).limit(limit)
    )
    keep = ('query_text', 'artists', 'titles', 'genres', 'decade', 'max_price', 'min_condition',
            'original_pressing_only')
    return [{'at': r.created_at, 'relax_attempts': r.relax_attempts,
             'asked': {k: v for k, v in r.constraints.items() if k in keep and v not in (None, [], '', False)}}
            for r in rows]


async def most_wanted(db: AsyncSession, limit: int) -> list[dict]:
    """Open wantlist entries (no copy matched yet), grouped by record: how many customers wait,
    and the most anyone is willing to pay. Current state, so no time window."""
    rows = await db.execute(
        select(first_written(WantlistItem.artist, WantlistItem.created_at, WantlistItem.id).label('artist'),
               first_written(WantlistItem.title, WantlistItem.created_at, WantlistItem.id).label('title'),
               func.count(func.distinct(WantlistItem.user_id)).label('customers'),
               func.max(WantlistItem.max_price).label('highest_max_price'))
        .where(WantlistItem.matched_item_id.is_(None))
        .group_by(WantlistItem.artist_norm, WantlistItem.title_norm)
        .order_by(func.count(func.distinct(WantlistItem.user_id)).desc(), WantlistItem.artist_norm,
                  WantlistItem.title_norm.nulls_first())
        .limit(limit)
    )
    return [r._asdict() for r in rows]


async def reservation_stats(db: AsyncSession, since: ColumnElement) -> dict:
    """Reservations created in the window, by CURRENT status (one row, COUNT … FILTER).

    release_expired first: expiry is lazy (Phase 6), so without it holds past their
    48 h would still be counted as active.
    """
    await release_expired(db)
    by = lambda status: func.count().filter(Reservation.status == status).label(status)  # noqa: E731
    row = (await db.execute(
        select(func.count().label('created'), by('active'), by('completed'), by('cancelled'), by('expired'))
        .where(Reservation.created_at > since)
    )).one()
    return row._asdict()


async def intent_counts(db: AsyncSession, since: ColumnElement) -> list[dict]:
    rows = await db.execute(
        select(ChatMessage.intent.label('name'), func.count().label('count'))
        .where(ChatMessage.intent.is_not(None), ChatMessage.created_at > since)
        .group_by(ChatMessage.intent).order_by(func.count().desc(), ChatMessage.intent)
    )
    return [r._asdict() for r in rows]


def rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None
