"""Staff analytics (spec 7.9). Staff only: the whole router requires the staff role.

All four endpoints take `days` (the window, default 30) and are thin: the SQL lives in
app/repositories/analytics.py.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func

from app.auth.deps import DbSession, require_staff
from app.repositories import analytics as q
from app.schemas.analytics import DemandOut, IntentsOut, ReservationStatsOut, UnmetDemandOut

router = APIRouter(prefix='/analytics', tags=['analytics'], dependencies=[Depends(require_staff)])

Days = Annotated[int, Query(ge=1, le=365, description='Look back this many days')]
Limit = Annotated[int, Query(ge=1, le=50, description='Items per list')]


def window(days: int):
    """`now() - N days` as SQL: the database clock decides, like everywhere else."""
    return func.now() - timedelta(days=days)


@router.get('/demand', response_model=DemandOut)
async def demand(db: DbSession, days: Days = 30, limit: Limit = 10) -> DemandOut:
    """What customers ask the assistant for: genres, artists, titles, decades (from SearchLog)."""
    since = window(days)
    return DemandOut(
        days=days, searches=await q.count_searches(db, since),
        genres=await q.top_values(db, 'genres', since, limit),
        artists=await q.top_values(db, 'artists', since, limit),
        titles=await q.top_values(db, 'titles', since, limit),
        decades=await q.top_decades(db, since, limit),
    )


@router.get('/unmet-demand', response_model=UnmetDemandOut)
async def unmet_demand(db: DbSession, days: Days = 30, limit: Limit = 10) -> UnmetDemandOut:
    """What customers wanted and the shop didn't have: zero-result searches (even after the
    relax loop) and the most wanted open wantlist entries. A buying list for staff."""
    since = window(days)
    searches = await q.count_searches(db, since)
    zero = await q.count_searches(db, since, zero_results=True)
    return UnmetDemandOut(
        days=days, searches=searches, zero_result_searches=zero, zero_result_rate=q.rate(zero, searches),
        genres=await q.top_values(db, 'genres', since, limit, zero_results=True),
        artists=await q.top_values(db, 'artists', since, limit, zero_results=True),
        titles=await q.top_values(db, 'titles', since, limit, zero_results=True),
        recent=await q.recent_zero_results(db, since, limit),
        most_wanted=await q.most_wanted(db, limit),
    )


@router.get('/reservations', response_model=ReservationStatsOut)
async def reservations(db: DbSession, days: Days = 30) -> ReservationStatsOut:
    """Reservations created in the window by current status, and how many turned into sales."""
    stats = await q.reservation_stats(db, window(days))
    closed = stats['completed'] + stats['cancelled'] + stats['expired']
    return ReservationStatsOut(days=days, **stats,
                               conversion_rate=q.rate(stats['completed'], stats['created']),
                               completion_rate_of_closed=q.rate(stats['completed'], closed))


@router.get('/intents', response_model=IntentsOut)
async def intents(db: DbSession, days: Days = 30) -> IntentsOut:
    """How customer requests split by intent (every chat turn, also off-topic ones)."""
    counts = await q.intent_counts(db, window(days))
    total = sum(c['count'] for c in counts)
    return IntentsOut(days=days, total=total,
                      intents=[{**c, 'share': q.rate(c['count'], total)} for c in counts])
