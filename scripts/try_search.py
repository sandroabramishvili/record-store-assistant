"""Try the hybrid search from the terminal, before the graph exists.

    python -m scripts.try_search "moody late night jazz" --max-price 40
    python -m scripts.try_search "70s jazz-funk" --decade 1970 --max-price 40 --min-condition VG+ --original-only
    python -m scripts.try_search "something more upbeat" --similar-to "Kind of Blue"
    python -m scripts.try_search "Fleetwood Mac" --artist "Fleetwood Mac" --user jazz.fan@example.com

--user loads that customer's profile, collection and wantlist: the taste score uses them,
and the store rules (filter_available) are applied exactly as the assistant will.
Uses one real Gemini embedding call per run.
"""

import argparse
import logging

from sqlalchemy import select

from app.ai.vectorstores import close_vector_stores, get_record_store
from app.db import SessionLocal, engine
from app.models import CollectionItem, TasteProfile, User, WantlistItem
from app.repositories.search import search_inventory
from app.services.availability import filter_available


async def load_customer(db, email: str | None) -> tuple[dict, set, set, set, set]:
    """profile, owned record ids, owned (artist, title) keys, collection artists, wantlist artists."""
    if not email:
        return {}, set(), set(), set(), set()
    user = await db.scalar(select(User).where(User.email == email))
    if user is None:
        raise SystemExit(f'No user {email}. Run: python -m scripts.seed')
    p = await db.get(TasteProfile, user.id)
    profile = {'favorite_genres': p.favorite_genres, 'disliked_genres': p.disliked_genres,
               'budget_cap': p.budget_cap, 'min_condition': p.min_condition} if p else {}
    owned = (await db.scalars(select(CollectionItem).where(CollectionItem.user_id == user.id))).all()
    wanted = (await db.scalars(select(WantlistItem).where(WantlistItem.user_id == user.id))).all()
    return (profile, {o.record_id for o in owned if o.record_id}, {(o.artist_norm, o.title_norm) for o in owned},
            {o.artist_norm for o in owned}, {w.artist_norm for w in wanted})


async def main(args: argparse.Namespace) -> None:
    constraints = {
        'query_text': args.query, 'genres': args.genre or [], 'artists': args.artist or [],
        'decade': args.decade, 'max_price': args.max_price, 'min_condition': args.min_condition,
        'format': args.format, 'original_pressing_only': args.original_only, 'similar_to': args.similar_to,
    }
    try:
        async with SessionLocal() as db:
            profile, owned_ids, owned_keys, coll_artists, want_artists = await load_customer(db, args.user)
            found = await search_inventory(db, get_record_store(), constraints, profile,
                                           collection_artists=coll_artists, wantlist_artists=want_artists)
        shown = filter_available(found, profile=profile, owned_record_ids=owned_ids, owned_keys=owned_keys,
                                 requested_genres=constraints['genres'])
        dropped = len(found) - len(shown)
        print(f'\n{len(found)} candidates from search, {len(shown)} pass the store rules'
              + (f' ({dropped} dropped: owned / disliked genre)' if dropped else '') + '\n')
        print(f'{"score":>6} {"sim":>6} {"taste":>6}  {"price":>8}  {"cond":<5} release')
        for c in shown[:args.top]:
            pressing = 'orig' if not c['is_reissue'] else f'{c["release_year"]} reissue'
            print(f'{c["score"]:6.3f} {c["similarity"]:6.3f} {c["taste"]:6.3f}  ${c["price"]:>7}  {c["media_condition"]:<5} '
                  f'{c["artist"]} — {c["title"]} ({c["original_year"]}, {pressing})')
        if not shown:
            print('Nothing matches → the assistant would relax the request, then offer the wantlist.')
    finally:
        await close_vector_stores()
        await engine.dispose()


if __name__ == '__main__':
    from app.loop import run

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('query')
    parser.add_argument('--max-price', type=float)
    parser.add_argument('--decade', type=int)
    parser.add_argument('--min-condition')
    parser.add_argument('--format')
    parser.add_argument('--genre', action='append', help='repeatable')
    parser.add_argument('--artist', action='append', help='repeatable')
    parser.add_argument('--original-only', action='store_true')
    parser.add_argument('--similar-to')
    parser.add_argument('--user', help='email of a customer, e.g. jazz.fan@example.com')
    parser.add_argument('--top', type=int, default=8)
    logging.basicConfig(level=logging.WARNING)
    run(main(parser.parse_args()))
