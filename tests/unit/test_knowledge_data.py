"""The knowledge-base files must cover the required topics and parse like the API validates them."""

import pytest

from scripts.knowledge_files import KNOWLEDGE_DIR, load_articles, parse_article

ARTICLES = load_articles()


def test_at_least_ten_articles_with_unique_titles():
    assert len(ARTICLES) >= 10
    titles = [a.title for a in ARTICLES]
    assert len(titles) == len(set(titles))


def test_every_topic_is_covered():
    assert {a.topic for a in ARTICLES} == {'grading', 'pressings', 'care', 'equipment', 'genre'}


@pytest.mark.parametrize('keyword', ['Goldmine', 'reissue', 'runout', '180', 'clean', 'store', '45 RPM', 'turntable'])
def test_required_subjects_are_present(keyword):
    """Required topics: grading, pressings, runouts, 180g, cleaning, storing, speeds, first turntable."""
    assert any(keyword.lower() in (a.title + a.content).lower() for a in ARTICLES), keyword


def test_demo_question_vg_plus_crackle_is_answered():
    grading = next(a for a in ARTICLES if a.topic == 'grading' and 'Goldmine' in a.title)
    assert 'VG+' in grading.content and 'crackle' in grading.content


def test_every_file_has_a_header():
    for path in KNOWLEDGE_DIR.glob('*.md'):
        parse_article(path.read_text(encoding='utf-8'))


@pytest.mark.parametrize('bad', [
    'no header at all',
    '---\ntitle: X\n',                                   # header never closed
    '---\ntitle: X\ntopic: cooking\n---\n' + 'x' * 50,   # unknown topic
])
def test_bad_files_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_article(bad)
