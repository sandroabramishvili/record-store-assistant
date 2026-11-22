import pytest

from app.services.normalize import normalize_name
from app.services.taste import taste_score

RELEASE = {'artist_norm': 'herbie hancock', 'genres': ['Jazz', 'Funk'], 'styles': ['jazz-funk', 'fusion']}


def test_taste_score():
    """Known profile + release: 2 of 4 tags are favorites, artist is in the collection."""
    profile = {'favorite_genres': ['jazz', 'jazz-funk', 'rock']}
    score = taste_score(RELEASE, profile, collection_artists={'herbie hancock'}, wantlist_artists=set())
    assert score == pytest.approx(0.7 * 0.5 + 0.3 * 1)   # 0.65


def test_wantlist_artist_counts_too():
    assert taste_score(RELEASE, {}, set(), {'herbie hancock'}) == pytest.approx(0.3)


def test_artist_matched_by_normalized_name():
    collection = {normalize_name('Herbie Hancock ')}
    assert taste_score(RELEASE, {}, collection, set()) == pytest.approx(0.3)


@pytest.mark.parametrize(
    ('release', 'profile', 'expected'),
    [
        ({'artist_norm': 'x', 'genres': [], 'styles': []}, {'favorite_genres': ['jazz']}, 0.0),   # no tags
        (RELEASE, {}, 0.0),                                                                        # empty profile
        (RELEASE, {'favorite_genres': ['jazz', 'funk', 'jazz-funk', 'fusion']}, 0.7),              # all tags
    ],
)
def test_edges(release, profile, expected):
    assert taste_score(release, profile, set(), set()) == pytest.approx(expected)


def test_score_is_between_0_and_1():
    best = taste_score(RELEASE, {'favorite_genres': ['jazz', 'funk', 'jazz-funk', 'fusion']}, {'herbie hancock'}, set())
    assert best == pytest.approx(1.0)
