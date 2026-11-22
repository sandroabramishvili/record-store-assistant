"""Taste score (spec section 8, point 8): how well a release fits the customer, 0.0 – 1.0. Computed by code."""

from collections.abc import Iterable

from app.services.normalize import normalize_tag

GENRE_WEIGHT = 0.7
ARTIST_WEIGHT = 0.3


def genre_match(release_tags: Iterable[str], favorite_genres: Iterable[str]) -> float:
    """Share of the release's genres/styles that are among the customer's favorites."""
    tags = {normalize_tag(t) for t in release_tags}            # "hip hop" = "hip-hop"
    if not tags:
        return 0.0
    favorites = {normalize_tag(g) for g in favorite_genres}
    return len(tags & favorites) / len(tags)


def taste_score(
    release: dict,
    profile: dict,
    collection_artists: set[str],   # artist_norm values from the collection
    wantlist_artists: set[str],     # artist_norm values from the wantlist
) -> float:
    """0.7 × genre_match + 0.3 × artist_affinity.

    artist_affinity = 1 if the release's artist is in the collection or on the wantlist.
    Artists are compared by their normalized names (`artist_norm`).
    """
    tags = [*release.get('genres', []), *release.get('styles', [])]
    affinity = 1.0 if release['artist_norm'] in (collection_artists | wantlist_artists) else 0.0
    score = GENRE_WEIGHT * genre_match(tags, profile.get('favorite_genres') or []) + ARTIST_WEIGHT * affinity
    return round(score, 4)
