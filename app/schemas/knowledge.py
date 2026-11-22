from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Topic = Literal['grading', 'pressings', 'care', 'equipment', 'genre']


class ArticleIn(BaseModel):
    """Create (POST) and full replace (PUT)."""

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    topic: Topic
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=50_000)]
    is_published: bool = True


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    topic: Topic
    content: str
    is_published: bool
    chunk_count: int
    updated_at: datetime


class SearchIn(BaseModel):
    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    k: Annotated[int, Field(ge=1, le=10)] = 3


class SearchHit(BaseModel):
    article_id: int
    title: str
    topic: str
    chunk: int
    score: float   # cosine similarity: 1 = same meaning, 0 = unrelated
    text: str
