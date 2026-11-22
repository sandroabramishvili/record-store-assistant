"""Genre / style tags are compared by spelling-insensitive key everywhere (fix for a real chat:
the catalog says "hip-hop" and "jazz rap", the customer and the LLM said "hip hop" and "jazz-rap",
and every search came back empty)."""

from decimal import Decimal

import pytest

from app.ai.nodes import relax_notes
from app.ai.prompts import understand_messages
from app.services.availability import filter_available
from app.services.normalize import normalize_tag
from app.services.taste import genre_match


@pytest.mark.parametrize('a, b', [('hip-hop', 'hip hop'), ('Hip Hop', 'hiphop'), ('jazz-rap', 'jazz rap'),
                                  ('Jazz-Funk ', 'jazz funk'), ('post-punk', 'Post Punk'), ('synth_pop', 'synth-pop')])
def test_same_tag_different_spelling(a, b):
    assert normalize_tag(a) == normalize_tag(b)


@pytest.mark.parametrize('a, b', [('hip-hop', 'trip-hop'), ('jazz', 'jazz rap'), ('soul', 'soul jazz')])
def test_different_tags_stay_different(a, b):
    assert normalize_tag(a) != normalize_tag(b)


def copy_tagged(item_id: int, genres: list[str], styles: list[str] = ()) -> dict:
    return {'item_id': item_id, 'record_id': item_id, 'artist_norm': f'a{item_id}', 'title_norm': f't{item_id}',
            'status': 'in_stock', 'price': Decimal('30'), 'media_condition': 'VG+', 'genres': genres,
            'styles': list(styles)}


def keep(profile: dict, items: list[dict], requested=()) -> list[int]:
    kept = filter_available(items, profile=profile, owned_record_ids=set(), owned_keys=set(), requested_genres=requested)
    return [i['item_id'] for i in kept]


def test_a_dislike_hides_the_tag_whatever_the_spelling():
    items = [copy_tagged(1, ['hip-hop']), copy_tagged(2, ['jazz'], ['jazz rap'])]
    assert keep({'disliked_genres': ['Hip Hop']}, items) == [2]
    assert keep({'disliked_genres': ['jazz-rap']}, items) == [1]


def test_asking_for_it_beats_a_dislike_whatever_the_spelling():
    """jazz.fan dislikes "hip-hop", but explicitly asks for "hip hop"."""
    assert keep({'disliked_genres': ['hip-hop']}, [copy_tagged(1, ['hip-hop'])], requested=['hip hop']) == [1]


def test_favorite_genre_counts_for_taste_whatever_the_spelling():
    assert genre_match(['hip-hop', 'jazz rap'], ['hip hop', 'Jazz-Rap']) == 1.0


def test_understand_prompt_lists_the_shops_own_tags():
    human = understand_messages('any hip hop?', shop_tags=['hip-hop', 'jazz rap', 'soul'])[-1][1]
    assert '<shop_genres>\nhip-hop, jazz rap, soul\n</shop_genres>' in human


def test_note_when_the_genre_filter_was_dropped():
    notes = relax_notes({'genres': ['abstract hip hop']}, {'genres': [], 'query_text': 'abstract hip hop'})
    assert notes == ['Nothing in stock is tagged abstract hip hop, so these are the closest records by sound '
                     'from other styles. Say so; do not present them as that genre.']
