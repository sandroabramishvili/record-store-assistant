"""Wantlist matching rules (spec 6.6): code, not the LLM, decides when a wanted record is in stock.

Pure functions: plain dicts in, answers out. No DB imports, so unit tests need nothing.
The DB side (loading entries/copies, saving `matched_item_id`) is in
app/repositories/wantlist.py.
"""

from collections.abc import Iterable
from decimal import Decimal

from app.services.conditions import condition_rank, meets_minimum


def entry_matches(entry: dict, copy: dict) -> bool:
    """Does this copy satisfy this wantlist entry?

    entry: artist_norm, title_norm (None = anything by the artist), max_price, min_condition
    copy:  artist_norm, title_norm, price, media_condition, status
    """
    return (
        copy['status'] == 'in_stock'
        and copy['artist_norm'] == entry['artist_norm']
        and (entry['title_norm'] is None or copy['title_norm'] == entry['title_norm'])
        and (entry['max_price'] is None or Decimal(str(copy['price'])) <= Decimal(str(entry['max_price'])))
        and meets_minimum(copy['media_condition'], entry['min_condition'])
    )


def find_matches(copy: dict, entries: Iterable[dict]) -> list[int]:
    """Ids of the entries this one copy satisfies (used when staff add a copy)."""
    return [e['id'] for e in entries if entry_matches(e, copy)]


def best_copy(entry: dict, copies: Iterable[dict]) -> int | None:
    """The copy to offer for an entry: cheapest, then best condition, then oldest id."""
    candidates = [c for c in copies if entry_matches(entry, c)]
    if not candidates:
        return None
    best = min(candidates, key=lambda c: (Decimal(str(c['price'])), -condition_rank(c['media_condition']), c['id']))
    return best['id']
