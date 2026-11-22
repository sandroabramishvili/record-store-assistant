"""Vector stores: where embeddings (meaning as numbers) are saved and searched.

Two PGVector collections in our own Postgres:
- `records`:   one vector per release (artist, title, genres, description...)
- `knowledge`: chunks of the knowledge-base articles

PGVector keeps them in two tables it creates itself (langchain_pg_collection,
langchain_pg_embedding). Alembic ignores those tables (see migrations/env.py).

One store object per collection for the whole app (created on first use):
each one holds a connection pool, so creating a new store per request would
open new pools again and again.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from langchain_core.embeddings import Embeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_postgres import PGVector

from app.config import settings

RECORDS_COLLECTION = 'records'
KNOWLEDGE_COLLECTION = 'knowledge'

_stores: dict[str, PGVector] = {}
_embeddings_override: Embeddings | None = None


@lru_cache
def _gemini_embeddings() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(
        model=settings.embedding_model,
        google_api_key=settings.google_api_key,
        output_dimensionality=settings.embedding_dimensions,
        # task_type is deliberately NOT set: the library then uses RETRIEVAL_DOCUMENT
        # when storing and RETRIEVAL_QUERY when searching, which matches better.
    )


def get_embeddings() -> Embeddings:
    """Gemini in the app; tests swap in a fake (no API calls, no cost) via use_embeddings()."""
    return _embeddings_override or _gemini_embeddings()


def _store(collection: str) -> PGVector:
    if collection not in _stores:
        _stores[collection] = PGVector(
            embeddings=get_embeddings(),
            collection_name=collection,
            connection=settings.psycopg_url,          # postgresql+psycopg://... (psycopg 3)
            embedding_length=settings.embedding_dimensions,  # fixed-size vector column
            use_jsonb=True,                            # metadata as JSONB → filterable
            async_mode=True,                           # aadd_documents / asimilarity_search
            create_extension=False,                    # our Alembic migration already did
            engine_args={'pool_pre_ping': True},
        )
    return _stores[collection]


def get_record_store() -> PGVector:
    return _store(RECORDS_COLLECTION)


def get_knowledge_store() -> PGVector:
    return _store(KNOWLEDGE_COLLECTION)


# FastAPI dependency shortcuts, like CurrentUser: `store: RecordStore` in an endpoint.
RecordStore = Annotated[PGVector, Depends(get_record_store)]
KnowledgeStore = Annotated[PGVector, Depends(get_knowledge_store)]


# ---------- lifecycle / tests ----------


async def close_vector_stores() -> None:
    """Close the stores' connection pools (app shutdown, end of each test)."""
    for store in _stores.values():
        engine = getattr(store, '_async_engine', None)
        if engine is not None:
            await engine.dispose()
    _stores.clear()


def use_embeddings(embeddings: Embeddings | None) -> None:
    """Swap the embedding model (tests: a deterministic fake). None = back to Gemini.

    Call before the stores are first used, or after close_vector_stores().
    """
    global _embeddings_override
    _embeddings_override = embeddings
