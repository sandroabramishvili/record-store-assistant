"""Fill the catalog, copies, demo users and release vectors from scripts/data/*.json.

    python -m scripts.seed_catalog                 # add what's missing (safe to run again)
    python -m scripts.seed_catalog --reset         # delete catalog + demo users, then refill
    python -m scripts.seed_catalog --vectors-only  # re-embed every release in the DB
    python -m scripts.seed_catalog --no-vectors    # SQL only (no Gemini calls)

Idempotent: a release is identified by (artist, title, release year, label), a user by
email. Existing ones are skipped, never duplicated or overwritten.
"""

import argparse
import json
import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.indexing import index_records
from app.ai.vectorstores import close_vector_stores, get_record_store
from app.auth.security import hash_password
from app.db import SessionLocal, engine
from app.models import CollectionItem, InventoryItem, Record, Reservation, TasteProfile, User, WantlistItem
from app.repositories.wantlist import refresh_matches
from app.services.normalize import normalize_name
from app.services.reservations import HOLD_HOURS

logger = logging.getLogger('seed_catalog')
DATA = Path(__file__).resolve().parent / 'data'


def load_json(name: str) -> list[dict]:
    return json.loads((DATA / name).read_text(encoding='utf-8'))


def _pressing_key(artist: str, title: str, release_year: int, label: str) -> tuple:
    return normalize_name(artist), normalize_name(title), release_year, label


async def _reset(db: AsyncSession, users: list[dict]) -> None:
    """Delete all releases (copies, reservations cascade) and the demo users."""
    await db.execute(delete(Record))
    await db.execute(delete(User).where(User.email.in_([u['email'] for u in users])))
    await db.commit()
    logger.info('reset: catalog and demo users deleted')


async def _seed_records(db: AsyncSession, records: list[dict]) -> tuple[list[Record], int, list[tuple[int, str]]]:
    """Insert missing releases with their copies.

    Returns (new records, number of new copies, [(copy id, reserved_by email)]).
    """
    existing = {_pressing_key(r.artist, r.title, r.release_year, r.label)
                for r in (await db.scalars(select(Record))).all()}
    created, reserved, copies = [], [], 0
    for data in records:
        if _pressing_key(data['artist'], data['title'], data['release_year'], data['label']) in existing:
            continue
        fields = {k: v for k, v in data.items() if k != 'copies'}
        record = Record(**fields, artist_norm=normalize_name(data['artist']), title_norm=normalize_name(data['title']),
                        is_reissue=data['release_year'] != data['original_year'])
        db.add(record)
        await db.flush()
        for c in data['copies']:
            item = InventoryItem(record_id=record.id, media_condition=c['media_condition'],
                                 sleeve_condition=c['sleeve_condition'], price=c['price'],
                                 status=c['status'], notes=c.get('notes'))
            db.add(item)
            await db.flush()
            copies += 1
            if c['status'] == 'reserved':
                reserved.append((item.id, c['reserved_by']))
        created.append(record)
    await db.commit()
    return created, copies, reserved


async def _seed_users(db: AsyncSession, users: list[dict]) -> int:
    """Create missing demo users with profile, collection (linked to the catalog) and wantlist."""
    by_names = {(r.artist_norm, r.title_norm): r for r in (await db.scalars(select(Record).order_by(Record.id))).all()}
    created = 0
    for u in users:
        if await db.scalar(select(User.id).where(User.email == u['email'])):
            continue
        user = User(email=u['email'], full_name=u['full_name'], role=u['role'],
                    hashed_password=hash_password(u['password']))
        p = u['profile']
        user.profile = TasteProfile(favorite_genres=p.get('favorite_genres', []), disliked_genres=p.get('disliked_genres', []),
                                    budget_cap=p.get('budget_cap'), min_condition=p.get('min_condition'))
        db.add(user)
        await db.flush()
        for item in u['collection']:
            artist_norm, title_norm = normalize_name(item['artist']), normalize_name(item['title'])
            record = by_names.get((artist_norm, title_norm))   # same auto-link as POST /collection
            db.add(CollectionItem(user_id=user.id, record_id=record.id if record else None,
                                  artist=record.artist if record else item['artist'],
                                  title=record.title if record else item['title'],
                                  artist_norm=artist_norm, title_norm=title_norm))
        for w in u['wantlist']:
            db.add(WantlistItem(user_id=user.id, artist=w['artist'], title=w.get('title'),
                                artist_norm=normalize_name(w['artist']),
                                title_norm=normalize_name(w['title']) if w.get('title') else None,
                                max_price=w.get('max_price'), min_condition=w.get('min_condition')))
        created += 1
    await db.commit()
    return created


async def _seed_reservations(db: AsyncSession, reserved: list[tuple[int, str]]) -> int:
    """Copies marked 'reserved' in the JSON get a real active reservation (48 h from now)."""
    for item_id, email in reserved:
        user_id = await db.scalar(select(User.id).where(User.email == email))
        db.add(Reservation(user_id=user_id, inventory_item_id=item_id, status='active',
                           expires_at=func.now() + timedelta(hours=HOLD_HOURS)))
    await db.commit()
    return len(reserved)


async def seed_catalog(*, reset: bool = False, vectors: bool = True, vectors_only: bool = False) -> dict:
    records, users = load_json('records.json'), load_json('demo_users.json')
    summary: dict[str, int] = {}
    async with SessionLocal() as db:
        if reset:
            await _reset(db, users)
            if vectors:
                await get_record_store().adelete_collection()   # drop every release vector
                await close_vector_stores()                     # recreated empty on next use

        if vectors_only:
            to_index = list((await db.scalars(select(Record).order_by(Record.id))).all())
        else:
            to_index, summary['copies added'], reserved = await _seed_records(db, records)
            summary['releases added'] = len(to_index)
            summary['demo users added'] = await _seed_users(db, users)
            summary['reservations added'] = await _seed_reservations(db, reserved)
            entries = list((await db.scalars(select(WantlistItem))).all())
            await refresh_matches(db, entries)
            summary['wantlist matches'] = sum(e.matched_item_id is not None for e in entries)

        if vectors:
            summary['release vectors written'] = await index_records(get_record_store(), to_index)

        summary['releases in DB'] = await db.scalar(select(func.count()).select_from(Record))
        summary['copies in DB'] = await db.scalar(select(func.count()).select_from(InventoryItem))
    return summary


async def main(args: argparse.Namespace) -> None:
    try:
        summary = await seed_catalog(reset=args.reset, vectors=not args.no_vectors, vectors_only=args.vectors_only)
        for key, value in summary.items():
            logger.info('%-24s %s', key, value)
    finally:
        await close_vector_stores()
        await engine.dispose()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reset', action='store_true', help='delete catalog and demo users first')
    parser.add_argument('--no-vectors', action='store_true', help='skip embeddings (no Gemini calls)')
    parser.add_argument('--vectors-only', action='store_true', help='only re-embed releases already in the DB')
    return parser.parse_args(argv)


if __name__ == '__main__':
    from app.loop import run

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    run(main(parse_args()))
