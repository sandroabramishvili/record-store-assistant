"""The store's hard rules (spec 4.4): the last gate before a copy is shown to a customer.

Pure function: everything comes in as arguments, nothing is read from the DB or an LLM.
The LLM can only talk about copies that survive this filter.
"""

from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, NotRequired, TypedDict

from app.services.conditions import meets_minimum
from app.services.normalize import normalize_tag


class Candidate(TypedDict):
    """One physical copy found by the search (only what the rules and the answer need)."""

    item_id: int
    record_id: int
    artist_norm: str
    title_norm: str
    status: str                      # in_stock / reserved / sold
    price: Decimal
    media_condition: str
    genres: list[str]
    styles: NotRequired[list[str]]
    reservation_expires_at: NotRequired[datetime | None]  # set when status == 'reserved'


def to_money(value: Any) -> Decimal | None:
    """Decimal from Decimal / str / int / float (via str, so 40.1 stays 40.1). None stays None."""
    return None if value is None else Decimal(str(value))


def is_in_stock(item: Candidate, now: datetime) -> bool:
    """`in_stock`, or `reserved` with a reservation that has already expired."""
    if item['status'] == 'in_stock':
        return True
    expires = item.get('reservation_expires_at')
    return item['status'] == 'reserved' and expires is not None and expires <= now


def filter_available(
    items: Iterable[Candidate],
    *,
    profile: dict,
    owned_record_ids: set[int],
    owned_keys: set[tuple[str, str]],   # {(artist_norm, title_norm)} for hand-entered collection items
    requested_genres: Iterable[str] = (),
    include_owned: bool = False,
    now: datetime | None = None,
) -> list[Candidate]:
    """Keep only copies that pass EVERY rule. Order of `items` is preserved."""
    now = now or datetime.now(timezone.utc)
    budget_cap = to_money(profile.get('budget_cap'))
    min_condition = profile.get('min_condition')
    # An explicit request beats a profile dislike: "I know I said no jazz, but show me jazz".
    # Tags compared by spelling-insensitive key: a dislike of "hip hop" also hides "hip-hop".
    requested = {normalize_tag(g) for g in requested_genres}
    disliked = {normalize_tag(g) for g in profile.get('disliked_genres') or []} - requested

    result = []
    for item in items:
        # 1. Stock: never reserved or sold (an expired reservation counts as released).
        if not is_in_stock(item, now):
            continue
        # 2. Ownership: by catalog id, or by normalized names for hand-entered records.
        if not include_owned and (
            item['record_id'] in owned_record_ids
            or (item['artist_norm'], item['title_norm']) in owned_keys
        ):
            continue
        # 3. Budget cap from the profile is hard (the request's max_price is soft, see relax).
        if budget_cap is not None and to_money(item['price']) > budget_cap:
            continue
        # 4. Minimum condition of the vinyl.
        if not meets_minimum(item['media_condition'], min_condition):
            continue
        # 5. Disliked genres (genres or styles), unless asked for explicitly in this request.
        tags = {normalize_tag(g) for g in [*item['genres'], *item.get('styles', [])]}
        if disliked & tags:
            continue
        result.append(item)
    return result
