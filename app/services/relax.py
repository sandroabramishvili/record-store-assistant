"""Loosening a search that found nothing (spec 4.3, `relax_constraints`).

Only SOFT constraints from the request are relaxed. Hard rules (stock, ownership,
profile budget_cap, min_condition) are never touched: they aren't even in this dict,
and the fields that are hard here (min_condition, include_owned, format, artists,
titles, original_pressing_only) are copied unchanged.
"""

import copy
from decimal import ROUND_HALF_UP, Decimal

from app.services.availability import to_money

MAX_RELAX_ATTEMPTS = 2
PRICE_STEP = Decimal('1.25')  # +25 %


def _raise_price(constraints: dict, budget_cap: Decimal | None) -> bool:
    price = to_money(constraints.get('max_price'))
    if price is None:
        return False
    new = (price * PRICE_STEP).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if budget_cap is not None:
        new = max(price, min(new, budget_cap))  # never above the cap, never lower than before
    if new == price:
        return False
    constraints['max_price'] = float(new)  # same type the LLM schema uses
    return True


def _drop_decade(constraints: dict) -> bool:
    if constraints.get('decade') is None:
        return False
    constraints['decade'] = None
    return True


def _drop_genres(constraints: dict) -> bool:
    """Stop FILTERING by genre, but keep the words in the similarity query, so the
    closest-sounding records still come first ("abstract hip hop" → J Dilla before Coltrane).

    Replaced a hand-made "related genres" table: it knew only the styles someone had
    typed into it, so an unknown one (a real chat: "hip hop") could never be loosened.
    """
    genres = constraints.get('genres') or []
    if not genres:
        return False
    query = constraints.get('query_text') or ''
    missing = [g for g in genres if g.lower() not in query.lower()]
    constraints['query_text'] = ' '.join([query, *missing]).strip()
    constraints['genres'] = []
    return True


def relax_constraints(constraints: dict, attempt: int, profile: dict) -> dict:
    """Return a NEW, looser copy of `constraints` (the input is never changed).

    attempt 1: max_price +25 % (capped at the profile budget_cap).
    attempt 2: drop the decade and the genre filter (genre words stay in the similarity query).
    If attempt 1 has nothing to loosen (no max_price, or already at the cap),
    it goes straight to the attempt-2 steps, so no search is wasted.
    If nothing could be loosened, the result equals the input → the graph
    should stop relaxing and offer the wantlist.
    """
    if not 1 <= attempt <= MAX_RELAX_ATTEMPTS:
        raise ValueError(f'attempt must be 1..{MAX_RELAX_ATTEMPTS}')
    relaxed = copy.deepcopy(constraints)
    if attempt == 1 and _raise_price(relaxed, to_money(profile.get('budget_cap'))):
        return relaxed
    decade_dropped = _drop_decade(relaxed)
    genres_dropped = _drop_genres(relaxed)
    if attempt == 2 and not (decade_dropped or genres_dropped):
        _raise_price(relaxed, to_money(profile.get('budget_cap')))  # last thing left to loosen
    return relaxed
