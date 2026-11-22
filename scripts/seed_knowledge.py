"""Load the knowledge articles from scripts/data/knowledge/*.md, chunk and embed them.

    python -m scripts.seed_knowledge            # add new / update changed articles (safe to run again)
    python -m scripts.seed_knowledge --reset    # delete all articles and their vectors, then refill
    python -m scripts.seed_knowledge --no-vectors

File format: see scripts/knowledge_files.py. Articles are matched by title. Unchanged ones are skipped; changed ones are re-indexed
(old chunks deleted, new ones added).
"""

import argparse
import logging

from sqlalchemy import delete, func, select

from app.ai.indexing import index_article
from app.ai.vectorstores import close_vector_stores, get_knowledge_store
from app.db import SessionLocal, engine
from app.models import KnowledgeArticle
from scripts.knowledge_files import load_articles

logger = logging.getLogger('seed_knowledge')


async def seed_knowledge(*, reset: bool = False, vectors: bool = True) -> dict:
    articles = load_articles()
    store = get_knowledge_store() if vectors else None
    summary = {'added': 0, 'updated': 0, 'unchanged': 0, 'chunks written': 0}
    async with SessionLocal() as db:
        if reset:
            await db.execute(delete(KnowledgeArticle))
            await db.commit()
            if store is not None:
                await store.adelete_collection()
                await close_vector_stores()
                store = get_knowledge_store()

        for data in articles:
            article = await db.scalar(select(KnowledgeArticle).where(KnowledgeArticle.title == data.title))
            if article is None:
                article = KnowledgeArticle(**data.model_dump(), chunk_count=0)
                db.add(article)
                await db.flush()
                summary['added'] += 1
            elif (article.topic, article.content) == (data.topic, data.content) and (article.chunk_count or not vectors):
                summary['unchanged'] += 1
                continue
            else:
                article.topic, article.content, article.is_published = data.topic, data.content, True
                summary['updated'] += 1
            if store is not None:
                summary['chunks written'] += await index_article(store, article)
            await db.commit()

        summary['articles in DB'] = await db.scalar(select(func.count()).select_from(KnowledgeArticle))
        summary['chunks in DB'] = await db.scalar(select(func.sum(KnowledgeArticle.chunk_count))) or 0
    return summary


async def main(args: argparse.Namespace) -> None:
    try:
        for key, value in (await seed_knowledge(reset=args.reset, vectors=not args.no_vectors)).items():
            logger.info('%-16s %s', key, value)
    finally:
        await close_vector_stores()
        await engine.dispose()


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reset', action='store_true', help='delete all articles and their vectors first')
    parser.add_argument('--no-vectors', action='store_true', help='skip embeddings (no Gemini calls)')
    return parser.parse_args(argv)


if __name__ == '__main__':
    from app.loop import run

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    run(main(parse_args()))
