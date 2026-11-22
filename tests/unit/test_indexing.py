from types import SimpleNamespace

from app.ai.indexing import (
    CHUNK_SIZE,
    article_chunk_ids,
    record_metadata,
    record_to_text,
    record_vector_id,
    split_article,
)
from scripts.knowledge_files import load_articles


def release(**overrides):
    r = dict(id=7, artist='Herbie Hancock', artist_norm='herbie hancock', title='Head Hunters', label='Columbia',
             release_year=1973, original_year=1973, format='LP', is_reissue=False,
             genres=['jazz', 'funk'], styles=['jazz-funk'], description='Long, hypnotic grooves.',
             tracklist=[{'position': 'A1', 'title': 'Chameleon'}, {'position': 'A2', 'title': 'Watermelon Man'}])
    return SimpleNamespace(**(r | overrides))


def test_record_text_describes_the_music():
    text = record_to_text(release())
    for part in ('Herbie Hancock', 'Head Hunters', '1973', 'Columbia', 'jazz-funk', 'hypnotic grooves',
                 'Chameleon', 'original pressing'):
        assert part in text


def test_record_text_never_contains_price_or_stock():
    """Prices and stock change constantly and are checked by SQL, not by similarity."""
    text = record_to_text(release()).lower()
    assert '$' not in text and 'in stock' not in text and 'price' not in text


def test_reissue_is_named_in_the_text():
    assert '2015 reissue' in record_to_text(release(release_year=2015, is_reissue=True))


def test_empty_tracklist_and_styles():
    text = record_to_text(release(tracklist=[], styles=[]))
    assert 'Tracks' not in text and 'Styles: -' in text


def test_metadata_and_ids_are_deterministic():
    meta = record_metadata(release())
    assert meta['record_id'] == 7 and meta['genres'] == ['jazz', 'funk'] and meta['original_year'] == 1973
    assert record_vector_id(7) == 'record-7'
    assert article_chunk_ids(3, 3) == ['kb-3-0', 'kb-3-1', 'kb-3-2']


def test_chunks_start_with_title_and_respect_size():
    content = ' '.join(f'Sentence number {i} about record care.' for i in range(150))
    chunks = split_article('How to Clean Your Records', content)
    assert len(chunks) > 3
    assert all(c.startswith('How to Clean Your Records\n\n') for c in chunks)
    assert all(len(c) <= CHUNK_SIZE + len('How to Clean Your Records\n\n') for c in chunks)


def test_chunks_overlap():
    content = ' '.join(f'w{i}' for i in range(600))
    first, second = split_article('T', content)[:2]
    assert first.split()[-1] in second.split()   # the end of one chunk appears again in the next


async def test_rate_limit_is_waited_out_and_retried():
    from app.ai.indexing import with_rate_limit_retry

    calls, waits = [], []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError('429 RESOURCE_EXHAUSTED ... Please retry in 12.5s.')
        return 'ok'

    async def fake_sleep(seconds):
        waits.append(seconds)

    assert await with_rate_limit_retry(flaky, sleep=fake_sleep) == 'ok'
    assert len(calls) == 3 and waits == [13.5, 13.5]   # Gemini's delay + 1 s margin


async def test_other_errors_are_not_retried():
    import pytest

    from app.ai.indexing import with_rate_limit_retry

    async def broken():
        raise ValueError('bad input')

    with pytest.raises(ValueError):
        await with_rate_limit_retry(broken, sleep=None)


def test_real_articles_split_into_several_chunks():
    for article in load_articles():
        assert 1 <= len(split_article(article.title, article.content)) <= 8, article.title
