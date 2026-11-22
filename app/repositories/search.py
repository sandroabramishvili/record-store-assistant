"""Hybrid inventory search (spec 4.3 `search_inventory`, 8.7): SQL first, vectors second, taste third.

1. SQL keeps only copies that CAN be sold under the strict conditions (in stock, price,
   condition, format, decade, original pressing, genres, artists).
2. Vector similarity ranks the releases that survived step 1 (never the whole catalog).
3. The taste score nudges the order toward the customer's profile.

The result is a list of candidate COPIES (flat dicts). The graph then passes them through
`filter_available` (Phase 5), which stays the final authority on the store rules.
"""

import logging
from decimal import Decimal

from langchain_postgres import PGVector
from sqlalchemy import func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.indexing import record_to_text
from app.models import InventoryItem, Record
from app.services.conditions import CONDITION_ORDER, condition_rank
from app.services.normalize import normalize_name
from app.services.ranking import rank
from app.services.reservations import release_expired
from app.services.taste import taste_score

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 20          # candidates handed to the graph (it shows ~5 after filtering)
MIN_TITLE_LENGTH = 4        # "Low" or "Ok" would match inside too many sentences


def _grades_at_least(minimum: str | None) -> list[str] | None:
    return None if minimum is None else CONDITION_ORDER[condition_rank(minimum):]


def _strictest_grade(*grades: str | None) -> str | None:
    present = [g for g in grades if g]
    return max(present, key=condition_rank) if present else None


def _lowest_price(*prices) -> Decimal | None:
    present = [Decimal(str(p)) for p in prices if p is not None]
    return min(present) if present else None


async def find_reference_releases(db: AsyncSession, similar_to: str | None) -> list[Record]:
    """Catalog releases named in `similar_to` ("something like Kind of Blue").

    Matched by normalized title appearing inside the normalized text, so
    "Miles Davis' Kind of Blue" and "kind of blue" both find it. All pressings returned.
    """
    if not similar_to:
        return []
    text = f' {normalize_name(similar_to)} '
    rows = await db.scalars(
        select(Record).where(
            func.length(Record.title_norm) >= MIN_TITLE_LENGTH,
            literal(text).contains(' ' + Record.title_norm + ' '),   # whole words only
        ).order_by(Record.id)
    )
    return list(rows.all())


def build_query_text(constraints: dict, references: list[Record]) -> str:
    """What gets embedded for the similarity search.

    With a known reference release, its catalog text (sound, genres, description) is a far
    better query than the customer's few words; the request text is added for the twist
    ("...but more upbeat").
    """
    request = constraints.get('query_text') or ' '.join(constraints.get('genres') or []) or 'records'
    if references:
        return f'{request}\nSimilar to:\n{record_to_text(references[0])}'
    return request


async def search_inventory(
    db: AsyncSession,
    store: PGVector,
    constraints: dict,
    profile: dict,
    *,
    collection_artists: set[str] = frozenset(),
    wantlist_artists: set[str] = frozenset(),
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    """Ranked candidate copies for a request. Empty list = nothing matches (→ relax)."""
    await release_expired(db)   # expired holds are back on sale

    # ---- 1. SQL: strict conditions ----
    stmt = (select(InventoryItem, Record)
            .join(Record, Record.id == InventoryItem.record_id)
            .where(InventoryItem.status == 'in_stock'))

    # Price: the request's max_price (soft, can be relaxed) AND the profile's budget_cap (hard).
    max_price = _lowest_price(constraints.get('max_price'), profile.get('budget_cap'))
    if max_price is not None:
        stmt = stmt.where(InventoryItem.price <= max_price)
    # Condition: the stricter of request and profile minimum (media condition).
    grades = _grades_at_least(_strictest_grade(constraints.get('min_condition'), profile.get('min_condition')))
    if grades is not None:
        stmt = stmt.where(InventoryItem.media_condition.in_(grades))
    if constraints.get('format'):
        stmt = stmt.where(Record.format == constraints['format'])
    if constraints.get('decade'):
        decade = constraints['decade']
        stmt = stmt.where(Record.original_year.between(decade, decade + 9))
    if constraints.get('original_pressing_only'):
        stmt = stmt.where(Record.is_reissue.is_(False))
    if constraints.get('genres'):
        genres = [g.strip().lower() for g in constraints['genres']]
        stmt = stmt.where(or_(Record.genres.overlap(genres), Record.styles.overlap(genres)))
    if constraints.get('artists'):
        stmt = stmt.where(Record.artist_norm.in_([normalize_name(a) for a in constraints['artists']]))
    if constraints.get('titles'):
        stmt = stmt.where(Record.title_norm.in_([normalize_name(t) for t in constraints['titles']]))

    # "Something like X": don't recommend X itself (any pressing).
    references = await find_reference_releases(db, constraints.get('similar_to'))
    if references:
        stmt = stmt.where(Record.id.not_in([r.id for r in references]))   # every pressing of it

    rows = (await db.execute(stmt)).all()
    if not rows:
        logger.info('search: 0 candidates for %s', constraints)
        return []

    # ---- 2. Vectors: rank only the releases that passed SQL ----
    record_ids = sorted({rec.id for _, rec in rows})
    hits = await store.asimilarity_search_with_score(
        build_query_text(constraints, references),
        k=len(record_ids),
        filter={'record_id': {'$in': record_ids}},
    )
    similarity = {doc.metadata['record_id']: 1 - distance for doc, distance in hits}   # distance → similarity

    # ---- 3. Taste + final ranking ----
    records = {rec.id: rec for _, rec in rows}
    taste = {
        rid: taste_score({'artist_norm': r.artist_norm, 'genres': r.genres, 'styles': r.styles},
                         profile, set(collection_artists), set(wantlist_artists))
        for rid, r in records.items()
    }
    candidates = [_to_candidate(item, rec) for item, rec in rows]
    ranked = rank(candidates, similarity, taste)[:limit]
    logger.info('search: %d candidates (%d releases) for %s', len(rows), len(record_ids), constraints)
    return ranked


def _to_candidate(item: InventoryItem, rec: Record) -> dict:
    """Flat dict for filter_available and the answer. Only what's needed (state stays small)."""
    return {
        'item_id': item.id, 'record_id': rec.id,
        'artist': rec.artist, 'title': rec.title, 'artist_norm': rec.artist_norm, 'title_norm': rec.title_norm,
        'label': rec.label, 'release_year': rec.release_year, 'original_year': rec.original_year,
        'format': rec.format, 'is_reissue': rec.is_reissue,
        'genres': list(rec.genres), 'styles': list(rec.styles),
        'status': item.status, 'price': item.price,
        'media_condition': item.media_condition, 'sleeve_condition': item.sleeve_condition,
    }
