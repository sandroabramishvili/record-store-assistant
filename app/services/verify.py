"""Answer verification (spec 4.5): code checks the LLM's draft before the customer sees it.

Checking that an id is in a list or that a number equals a price is deterministic,
so it's done in Python, not by a second LLM. Each problem becomes one readable
issue string; the graph passes them back to the LLM for a retry.
"""

import re
from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal

from app.services.normalize import normalize_name

_NUMBER = r'(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)'  # 1,200.50 | 38 | 38.00 | 38,50
PRICE_RE = re.compile(
    rf'\$\s?{_NUMBER}'                       # $38, $ 38.00, $1,200
    rf'|{_NUMBER}\s?(?:USD|dollars?)\b'      # 38 USD, 38 dollars
    rf'|\bUSD\s?{_NUMBER}',                  # USD 38
    re.IGNORECASE,
)

# A sentence with one of these phrases claims the store sells the record it names.
STOCK_CLAIM_RE = re.compile(
    r"\b(in stock|available|we have|we've got|we carry|for sale|on the shelf|you can (?:buy|reserve))\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+|\n+')
MIN_TITLE_LENGTH = 4  # very short titles ("Low", "Ok") match ordinary words too often


def parse_amount(raw: str) -> tuple[Decimal, bool]:
    """'1,200.50' → (1200.50, True); '38,50' → (38.50, True); '38' → (38, False).

    The bool says whether the text had cents.
    """
    if re.fullmatch(r'\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?', raw):
        raw = raw.replace(',', '')           # thousands separator
    else:
        raw = raw.replace(',', '.')          # decimal comma
    return Decimal(raw), '.' in raw


def amount_matches(amount: Decimal, has_cents: bool, allowed: set[Decimal]) -> bool:
    """Exact match, or a whole-dollar amount that is a real price rounded ($38 for $37.99 or $38.00).

    With cents written out, it must be exact: "$38.00" for a $37.99 copy is a false claim.
    """
    for price in allowed:
        if amount == price:
            return True
        if not has_cents and amount == price.quantize(Decimal('1'), rounding=ROUND_HALF_UP):
            return True
    return False


def find_amounts(message: str) -> list[tuple[str, Decimal, bool]]:
    """Every money amount in the text: (as written, value, has_cents)."""
    found = []
    for m in PRICE_RE.finditer(message):
        raw = next(g for g in m.groups() if g)
        value, has_cents = parse_amount(raw)
        found.append((m.group(0), value, has_cents))
    return found


def unsupported_stock_claims(
    message: str,
    catalog_titles: Mapping[str, set[int]],   # title_norm → record ids with that title
    available_record_ids: set[int],
) -> list[str]:
    """Titles named in a sentence that claims stock, but with no available copy.

    Titles mentioned only for context ("this sounds like Kind of Blue") pass,
    because that sentence makes no stock claim.
    """
    issues = []
    for sentence in _SENTENCE_SPLIT.split(message):
        if not STOCK_CLAIM_RE.search(sentence):
            continue
        padded = f' {normalize_name(sentence)} '
        for title, record_ids in catalog_titles.items():
            if len(title) >= MIN_TITLE_LENGTH and f' {title} ' in padded and not (record_ids & available_record_ids):
                issues.append(f'"{title}" is described as available, but no copy of it is in stock')
    return issues


def verify_draft(
    *,
    featured_ids: Iterable[int],
    message: str,
    available_by_id: Mapping[int, dict],      # item_id → {'price': Decimal, 'record_id': int, ...}
    extra_amounts: Iterable[Decimal] = (),    # other numbers the answer may quote (budget cap, max_price)
    catalog_titles: Mapping[str, set[int]] | None = None,
) -> list[str]:
    """Return a list of issues. Empty list = the draft may be sent."""
    featured_ids = list(featured_ids)
    issues: list[str] = []

    # 1. Only copies that passed the availability filter may be featured.
    for item_id in featured_ids:
        if item_id not in available_by_id:
            issues.append(f'item {item_id} is not in the provided list of available copies')

    # 2. Every amount must be a featured copy's price (or a value from the state).
    allowed = {Decimal(str(available_by_id[i]['price'])) for i in featured_ids if i in available_by_id}
    allowed |= {Decimal(str(a)) for a in extra_amounts}
    for written, value, has_cents in find_amounts(message):
        if not amount_matches(value, has_cents, allowed):
            issues.append(f'amount {written!r} does not match any featured price')

    # 3. No "in stock" claims about releases without an available copy.
    if catalog_titles:
        available_record_ids = {item['record_id'] for item in available_by_id.values()}
        issues += unsupported_stock_claims(message, catalog_titles, available_record_ids)

    return issues
