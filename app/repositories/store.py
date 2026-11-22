"""StoreRepository: everything the graph needs from the database and the vector stores.

Graph nodes never open sessions or create stores themselves; they call these methods
through `Deps` (app/ai/deps.py). That keeps nodes small and lets tests swap pieces.

Two rules:
- Each method uses its own short-lived session. The graph can pause for minutes
  (waiting for "confirm"), and no session may stay open across that pause.
- Everything returned is JSON-safe (dicts, lists, str, int, bool; prices as strings
  like "38.00"), because it goes into the graph state, which is checkpointed.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from decimal import Decimal
from typing import Any

from langchain_postgres import PGVector
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    CollectionItem,
    InventoryItem,
    Record,
    Reservation,
    SearchLog,
    TasteProfile,
    WantlistItem,
)
from app.repositories.search import catalog_tags, search_inventory
from app.repositories.wantlist import refresh_matches
from app.services.customer import CustomerContext
from app.services.normalize import normalize_name
from app.services.reservations import release_expired, reserve_item

SessionFactory = Callable[[], AsyncSession]


def money(value: Any) -> str | None:
    """Decimal → "38.00" (exact, JSON-safe). None stays None."""
    return None if value is None else str(Decimal(str(value)).quantize(Decimal('0.01')))


def _json_safe(candidate: dict) -> dict:
    return candidate | {'price': money(candidate['price'])}


class StoreRepository:
    def __init__(self, session_factory: SessionFactory, record_store: PGVector, knowledge_store: PGVector):
        self.session = session_factory
        self.records = record_store
        self.knowledge = knowledge_store

    # ---------- customer ----------

    async def load_context(self, user_id: int) -> CustomerContext:
        async with self.session() as db:
            p = await db.get(TasteProfile, user_id)
            owned = (await db.scalars(select(CollectionItem).where(CollectionItem.user_id == user_id))).all()
            wanted = (await db.scalars(select(WantlistItem.artist_norm).where(WantlistItem.user_id == user_id))).all()
        profile = {} if p is None else {
            'favorite_genres': list(p.favorite_genres), 'disliked_genres': list(p.disliked_genres),
            'budget_cap': money(p.budget_cap), 'min_condition': p.min_condition,
        }
        return CustomerContext(
            profile=profile,
            collection=[{'record_id': o.record_id, 'artist_norm': o.artist_norm, 'title_norm': o.title_norm} for o in owned],
            wantlist_artists=sorted(set(wanted)),
        )

    # ---------- search ----------

    async def search(self, constraints: dict, ctx: CustomerContext, *, limit: int = 20) -> list[dict]:
        """Hybrid search (Phase 8). Candidates still need filter_available."""
        async with self.session() as db:
            found = await search_inventory(
                db, self.records, constraints, ctx.profile,
                collection_artists=ctx.collection_artists, wantlist_artists=set(ctx.wantlist_artists), limit=limit,
            )
        return [_json_safe(c) for c in found]

    async def get_items(self, item_ids: Sequence[int]) -> dict[int, dict]:
        """Current state of specific copies (status may have changed since they were shown)."""
        if not item_ids:
            return {}
        async with self.session() as db:
            await release_expired(db)
            rows = await db.execute(
                select(InventoryItem, Record).join(Record, Record.id == InventoryItem.record_id)
                .where(InventoryItem.id.in_(list(item_ids)))
            )
            return {item.id: _copy_dict(item, rec) for item, rec in rows}

    async def log_search(self, user_id: int, intent: str, constraints: dict, results_count: int,
                         relax_attempts: int) -> None:
        """One row per search (spec 6.9); zero-result rows show staff what customers miss."""
        async with self.session() as db:
            db.add(SearchLog(user_id=user_id, intent=intent, constraints=constraints,
                             results_count=results_count, relax_attempts=relax_attempts))
            await db.commit()

    # ---------- actions ----------

    async def active_reservation_count(self, user_id: int) -> int:
        async with self.session() as db:
            await release_expired(db)
            return await db.scalar(select(func.count()).select_from(Reservation).where(
                Reservation.user_id == user_id, Reservation.status == 'active'))

    async def reserve(self, user_id: int, item_id: int) -> dict:
        """THE reservation service (6.2). Raises ReservationError subclasses on failure."""
        async with self.session() as db:
            r = await reserve_item(db, user_id, item_id)
        return {'reservation_id': r.id, 'inventory_item_id': r.inventory_item_id,
                'expires_at': r.expires_at.isoformat()}

    async def add_wantlist(self, user_id: int, artist: str, title: str | None = None,
                           max_price: Any = None, min_condition: str | None = None) -> dict:
        """Add an entry (or return the existing one) and check current stock right away."""
        artist_norm = normalize_name(artist)
        title_norm = normalize_name(title) if title else None
        async with self.session() as db:
            entry = await db.scalar(select(WantlistItem).where(
                WantlistItem.user_id == user_id, WantlistItem.artist_norm == artist_norm,
                WantlistItem.title_norm.is_(None) if title_norm is None else WantlistItem.title_norm == title_norm,
            ))
            created = entry is None
            if created:
                entry = WantlistItem(user_id=user_id, artist=artist, title=title, artist_norm=artist_norm,
                                     title_norm=title_norm, max_price=max_price, min_condition=min_condition)
                db.add(entry)
                await db.commit()
            await refresh_matches(db, [entry])
            return {'wantlist_id': entry.id, 'artist': entry.artist, 'title': entry.title,
                    'max_price': money(entry.max_price), 'min_condition': entry.min_condition,
                    'matched_item_id': entry.matched_item_id, 'created': created}

    # ---------- knowledge / record info ----------

    async def search_knowledge(self, query: str, *, k: int | None = None,
                               min_score: float | None = None) -> list[dict]:
        """Top chunks above the score threshold (spec 8.9). Empty = the KB has no answer."""
        k = k or settings.knowledge_top_k
        min_score = settings.knowledge_min_score if min_score is None else min_score
        hits = await self.knowledge.asimilarity_search_with_score(query, k=k)
        return [
            {'article_id': d.metadata['article_id'], 'title': d.metadata['title'], 'topic': d.metadata['topic'],
             'score': round(1 - distance, 4), 'text': d.page_content}
            for d, distance in hits if 1 - distance >= min_score
        ]

    async def record_details(self, *, titles: Iterable[str] = (), artists: Iterable[str] = (),
                             limit: int = 6) -> list[dict]:
        """Catalog facts for record_info answers: every pressing of the named records + copies in stock."""
        title_norms = [normalize_name(t) for t in titles]
        artist_norms = [normalize_name(a) for a in artists]
        if not title_norms and not artist_norms:
            return []
        async with self.session() as db:
            await release_expired(db)
            stmt = select(Record).order_by(Record.artist_norm, Record.title_norm, Record.release_year).limit(limit)
            if title_norms:
                stmt = stmt.where(Record.title_norm.in_(title_norms))
            if artist_norms:
                stmt = stmt.where(Record.artist_norm.in_(artist_norms))
            records = (await db.scalars(stmt)).all()
            copies = (await db.scalars(select(InventoryItem).where(
                InventoryItem.record_id.in_([r.id for r in records]), InventoryItem.status == 'in_stock'
            ).order_by(InventoryItem.price))).all() if records else []
        by_record: dict[int, list[dict]] = {}
        for c in copies:
            by_record.setdefault(c.record_id, []).append(
                {'id': c.id, 'media_condition': c.media_condition, 'sleeve_condition': c.sleeve_condition,
                 'price': money(c.price)})
        return [
            {'record_id': r.id, 'artist': r.artist, 'title': r.title, 'label': r.label,
             'catalog_number': r.catalog_number, 'release_year': r.release_year, 'original_year': r.original_year,
             'pressing': 'reissue' if r.is_reissue else 'original', 'format': r.format, 'country': r.country,
             'genres': list(r.genres), 'styles': list(r.styles), 'description': r.description,
             'tracklist': [t['title'] for t in r.tracklist or []], 'copies_in_stock': by_record.get(r.id, [])}
            for r in records
        ]

    async def catalog_tags(self) -> list[str]:
        """The shop's genre/style vocabulary, as spelled in the catalog (for the UNDERSTAND prompt)."""
        async with self.session() as db:
            return await catalog_tags(db)

    async def catalog_titles(self) -> dict[str, list[int]]:
        """title_norm → record ids, for verify_draft's "in stock" claim check (Phase 5)."""
        async with self.session() as db:
            rows = (await db.execute(select(Record.title_norm, Record.id))).all()
        titles: dict[str, list[int]] = {}
        for title, record_id in rows:
            titles.setdefault(title, []).append(record_id)
        return titles

    # ---------- collection ----------

    async def collection_stats(self, user_id: int) -> dict:
        """Counts by genre and decade (catalog-linked items only), like GET /collection."""
        async with self.session() as db:
            rows = (await db.execute(
                select(CollectionItem.artist, CollectionItem.title, Record.genres, Record.original_year)
                .outerjoin(Record, Record.id == CollectionItem.record_id)
                .where(CollectionItem.user_id == user_id)
            )).all()
        genres: Counter[str] = Counter()
        decades: Counter[str] = Counter()
        for _, _, g, year in rows:
            if year is not None:
                genres.update(g)
                decades[f'{year // 10 * 10}s'] += 1
        return {'total': len(rows), 'by_genre': dict(genres.most_common()), 'by_decade': dict(sorted(decades.items())),
                'records': [f'{a} — {t}' for a, t, _, _ in rows]}


def _copy_dict(item: InventoryItem, rec: Record) -> dict:
    return {
        'item_id': item.id, 'record_id': rec.id, 'artist': rec.artist, 'title': rec.title,
        'artist_norm': rec.artist_norm, 'title_norm': rec.title_norm, 'original_year': rec.original_year,
        'release_year': rec.release_year, 'is_reissue': rec.is_reissue, 'format': rec.format,
        'genres': list(rec.genres), 'styles': list(rec.styles), 'status': item.status, 'price': money(item.price),
        'media_condition': item.media_condition, 'sleeve_condition': item.sleeve_condition,
    }
