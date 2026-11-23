"""Turning catalog rows and articles into vectors.

Releases: one release = one document (short enough, no chunking), id `record-{id}`.
Articles: split into overlapping chunks, ids `kb-{article_id}-{n}`.

Deterministic ids mean "index again" overwrites instead of duplicating, and deleting
needs no lookup. The text-building functions are pure (no DB) and unit-tested.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from langchain_core.documents import Document
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

CHUNK_SIZE = 700       # characters (500-800 works well)
CHUNK_OVERLAP = 90     # 80-100 is enough, so a sentence cut at a boundary appears in both chunks
_splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

# ---------- rate limits ----------
# Gemini's free tier allows ~100 embedding requests per minute, and every text in a
# batch counts as one request. Bulk indexing (the seed scripts) therefore goes in
# small batches and, when Gemini answers 429 "RESOURCE_EXHAUSTED", waits and retries.

BATCH_SIZE = 25
MAX_RETRIES = 5


def rate_limit_delay(error: Exception) -> float | None:
    """Seconds to wait if this is a rate-limit error (Gemini says how long), else None."""
    message = str(error)
    if '429' not in message and 'RESOURCE_EXHAUSTED' not in message:
        return None
    match = re.search(r'retry in ([\d.]+)s', message)
    return float(match.group(1)) + 1 if match else 60.0


async def with_rate_limit_retry[T](make_call: Callable[[], Awaitable[T]], *, sleep=asyncio.sleep) -> T:
    """Run `make_call()`; on a rate-limit error wait as long as Gemini asks, then try again."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await make_call()
        except Exception as error:
            delay = rate_limit_delay(error)
            if delay is None or attempt == MAX_RETRIES:
                raise
            logger.warning('Embedding rate limit reached; waiting %.0f s (attempt %d/%d)', delay, attempt, MAX_RETRIES)
            await sleep(delay)
    raise AssertionError('unreachable')


# ---------- releases ----------


def record_vector_id(record_id: int) -> str:
    return f'record-{record_id}'


def record_to_text(r: Any) -> str:
    """The text that gets embedded for a release: everything that describes how it sounds.

    Prices, conditions and stock are deliberately NOT included: they change all the
    time and are checked by SQL, never by similarity.
    """
    pressing = 'original pressing' if r.release_year == r.original_year else f'{r.release_year} reissue'
    parts = [
        f'{r.artist} — {r.title} ({r.original_year}), {r.format}, {pressing}, label {r.label}.',
        f'Genres: {", ".join(r.genres) or "-"}. Styles: {", ".join(r.styles) or "-"}.',
        r.description,
    ]
    tracks = [t['title'] for t in (r.tracklist or [])]
    if tracks:
        parts.append(f'Tracks: {"; ".join(tracks)}.')
    return '\n'.join(parts)


def record_metadata(r: Any) -> dict:
    """Stored next to the vector (JSONB), used to filter the search."""
    return {
        'record_id': r.id,
        'artist_norm': r.artist_norm,
        'genres': list(r.genres),
        'styles': list(r.styles),
        'original_year': r.original_year,
        'format': r.format,
        'is_reissue': r.is_reissue,
    }


def record_document(r: Any) -> Document:
    return Document(page_content=record_to_text(r), metadata=record_metadata(r))


async def index_records(store: PGVector, records: Iterable[Any]) -> int:
    """Embed and store (or overwrite) releases, in rate-limit-friendly batches. Returns how many."""
    records = list(records)
    for start in range(0, len(records), BATCH_SIZE):
        batch = records[start:start + BATCH_SIZE]
        docs, ids = [record_document(r) for r in batch], [record_vector_id(r.id) for r in batch]
        await with_rate_limit_retry(lambda: store.aadd_documents(docs, ids=ids))
    return len(records)


async def delete_record_vector(store: PGVector, record_id: int) -> None:
    await store.adelete(ids=[record_vector_id(record_id)])


# ---------- knowledge articles ----------


def article_chunk_ids(article_id: int, count: int) -> list[str]:
    return [f'kb-{article_id}-{i}' for i in range(count)]


def split_article(title: str, content: str) -> list[str]:
    """Overlapping chunks, each starting with the article title.

    The title gives every chunk its context: a chunk that only says "it may tick in
    quiet passages" is much easier to find when it also says "The Goldmine Grading Scale".
    """
    return [f'{title}\n\n{chunk}' for chunk in _splitter.split_text(content)]


async def index_article(store: PGVector, article: Any) -> int:
    """(Re)index one article: delete its old chunks, add the new ones.

    Sets `article.chunk_count`; the caller commits. Unpublished articles get no
    chunks, so the assistant can never quote them. Returns the new chunk count.
    """
    await delete_article_vectors(store, article.id, article.chunk_count)
    if not article.is_published:
        article.chunk_count = 0
        return 0
    chunks = split_article(article.title, article.content)
    meta = {'article_id': article.id, 'topic': article.topic, 'title': article.title}
    docs = [Document(page_content=c, metadata=meta | {'chunk': i}) for i, c in enumerate(chunks)]
    ids = article_chunk_ids(article.id, len(chunks))
    await with_rate_limit_retry(lambda: store.aadd_documents(docs, ids=ids))
    article.chunk_count = len(chunks)
    return len(chunks)


async def delete_article_vectors(store: PGVector, article_id: int, chunk_count: int) -> None:
    if chunk_count:
        await store.adelete(ids=article_chunk_ids(article_id, chunk_count))
