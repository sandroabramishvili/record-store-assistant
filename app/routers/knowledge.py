"""Knowledge base, staff only. Every change re-indexes the article's chunks.

Unlike releases, an article that can't be indexed returns an error: the article's
whole purpose is to be found by the assistant, so a silent failure would hide it.
"""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from app.ai.indexing import delete_article_vectors, index_article
from app.ai.vectorstores import KnowledgeStore
from app.auth.deps import DbSession, require_staff
from app.models import KnowledgeArticle
from app.schemas.knowledge import ArticleIn, ArticleOut, SearchHit, SearchIn

router = APIRouter(prefix='/knowledge', tags=['knowledge'], dependencies=[Depends(require_staff)])


async def _get_or_404(db: DbSession, article_id: int) -> KnowledgeArticle:
    article = await db.get(KnowledgeArticle, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Article not found')
    return article


@router.get('/articles', response_model=list[ArticleOut])
async def list_articles(db: DbSession) -> list[KnowledgeArticle]:
    return list((await db.scalars(select(KnowledgeArticle).order_by(KnowledgeArticle.topic, KnowledgeArticle.title))).all())


@router.post('/articles', response_model=ArticleOut, status_code=status.HTTP_201_CREATED)
async def create_article(data: ArticleIn, db: DbSession, store: KnowledgeStore) -> KnowledgeArticle:
    article = KnowledgeArticle(**data.model_dump(), chunk_count=0)
    db.add(article)
    await db.flush()                      # get the id for the chunk ids
    await index_article(store, article)   # chunk + embed; sets chunk_count
    await db.commit()                     # only after indexing worked
    await db.refresh(article)             # chunk_count change → DB refreshed updated_at
    return article


@router.put('/articles/{article_id}', response_model=ArticleOut)
async def update_article(article_id: int, data: ArticleIn, db: DbSession, store: KnowledgeStore) -> KnowledgeArticle:
    article = await _get_or_404(db, article_id)
    for field, value in data.model_dump().items():
        setattr(article, field, value)
    await index_article(store, article)   # old chunks deleted, new ones added
    await db.commit()
    await db.refresh(article)             # updated_at was set by the database
    return article


@router.delete('/articles/{article_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(article_id: int, db: DbSession, store: KnowledgeStore) -> Response:
    article = await _get_or_404(db, article_id)
    await delete_article_vectors(store, article.id, article.chunk_count)
    await db.delete(article)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post('/search', response_model=list[SearchHit])
async def search(data: SearchIn, store: KnowledgeStore) -> list[SearchHit]:
    """Staff test search: what would the assistant find for this question?"""
    hits = await store.asimilarity_search_with_score(data.query, k=data.k)
    return [
        SearchHit(
            article_id=doc.metadata['article_id'], title=doc.metadata['title'], topic=doc.metadata['topic'],
            chunk=doc.metadata['chunk'], score=round(1 - distance, 4), text=doc.page_content,
        )
        for doc, distance in hits
    ]
