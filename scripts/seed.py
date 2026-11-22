"""One command for everything: catalog, copies, demo users, knowledge base and all vectors.

    python -m scripts.seed            # safe to run again (adds only what's missing)
    python -m scripts.seed --reset    # start over from the JSON / Markdown files
    python -m scripts.seed --no-vectors
"""

import argparse
import logging

from app.ai.vectorstores import close_vector_stores
from app.db import engine
from scripts.seed_catalog import seed_catalog
from scripts.seed_knowledge import seed_knowledge

logger = logging.getLogger('seed')


async def main(args: argparse.Namespace) -> None:
    try:
        catalog = await seed_catalog(reset=args.reset, vectors=not args.no_vectors)
        knowledge = await seed_knowledge(reset=args.reset, vectors=not args.no_vectors)
        for section, summary in (('catalog', catalog), ('knowledge', knowledge)):
            for key, value in summary.items():
                logger.info('%-9s %-24s %s', section, key, value)
    finally:
        await close_vector_stores()
        await engine.dispose()


if __name__ == '__main__':
    from app.loop import run

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reset', action='store_true')
    parser.add_argument('--no-vectors', action='store_true')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    run(main(parser.parse_args()))
