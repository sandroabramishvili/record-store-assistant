from datetime import datetime

from sqlalchemy import CheckConstraint, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class KnowledgeArticle(Base):
    __tablename__ = 'knowledge_articles'
    __table_args__ = (
        CheckConstraint("topic IN ('grading', 'pressings', 'care', 'equipment', 'genre')", name='topic_valid'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    topic: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    is_published: Mapped[bool] = mapped_column(default=True, server_default='true')
    # Not in the spec: how many vector chunks this article has, so the old ones
    # (ids kb-{id}-0 .. kb-{id}-{n-1}) can be deleted when the article is re-indexed.
    chunk_count: Mapped[int] = mapped_column(default=0, server_default='0')
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
