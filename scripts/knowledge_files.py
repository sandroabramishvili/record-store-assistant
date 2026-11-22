"""Reading knowledge articles from scripts/data/knowledge/*.md (pure: no DB, unit-tested).

Each file starts with a small header:

    ---
    title: The Goldmine Grading Scale
    topic: grading
    ---
    (article text, Markdown)
"""

from pathlib import Path

from app.schemas.knowledge import ArticleIn

KNOWLEDGE_DIR = Path(__file__).resolve().parent / 'data' / 'knowledge'


def parse_article(text: str) -> ArticleIn:
    """Split the `---` header from the body and validate it like the API does."""
    lines = text.lstrip('﻿').splitlines()
    stripped = [line.strip() for line in lines]
    if not lines or stripped[0] != '---' or '---' not in stripped[1:]:
        raise ValueError('article must start with a --- header --- block')
    end = 1 + stripped[1:].index('---')
    header = {k.strip(): v.strip() for k, v in (line.split(':', 1) for line in lines[1:end] if ':' in line)}
    body = '\n'.join(lines[end + 1:]).strip()
    return ArticleIn(title=header.get('title', ''), topic=header.get('topic', ''), content=body)


def load_articles(directory: Path = KNOWLEDGE_DIR) -> list[ArticleIn]:
    return [parse_article(p.read_text(encoding='utf-8')) for p in sorted(directory.glob('*.md'))]
