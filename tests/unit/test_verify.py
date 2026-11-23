from decimal import Decimal

import pytest

from app.services.normalize import normalize_name
from app.services.verify import find_amounts, verify_draft

AVAILABLE = {
    57: {'price': Decimal('38.00'), 'record_id': 5},
    58: {'price': Decimal('37.99'), 'record_id': 6},
    59: {'price': Decimal('1200.00'), 'record_id': 7},
}
CATALOG_TITLES = {
    normalize_name('Head Hunters'): {5},
    normalize_name('Thrust'): {6},
    normalize_name('Kind of Blue'): {8},   # in the catalog, but no copy available
}


def check(message, featured=(57,), **kwargs):
    return verify_draft(featured_ids=featured, message=message, available_by_id=AVAILABLE, **kwargs)


def test_verify_rejects_unavailable_item():
    issues = check('Here are two great picks.', featured=[57, 91])
    assert issues == ['item 91 is not in the provided list of available copies']


def test_verify_rejects_invented_price():
    """Prices: "$25" when the featured copy costs $38.00 fails; "$38" passes."""
    assert check('This NM copy is $25.') == ["amount '$25' does not match any featured price"]
    assert check('This NM copy is $38.') == []
    assert check('This NM copy is $38.00.') == []


@pytest.mark.parametrize(
    ('message', 'ok'),
    [
        ('Only $38!', True),           # rounded 37.99 → 38 is fine without cents
        ('Only $37.99!', True),
        ('Only $38.00!', False),       # cents written out must be exact
        ('Only $37!', False),
        ('Only 37.99 USD.', True),
        ('Only 38 dollars.', True),
        ('Only USD 37.99.', True),
        ('Only $37,99.', True),        # decimal comma
    ],
)
def test_rounding_rules(message, ok):
    assert (check(message, featured=[58]) == []) is ok


def test_thousands_separator():
    assert find_amounts('a rare one at $1,200.00')[0][1] == Decimal('1200.00')
    assert check('A first pressing at $1,200.', featured=[59]) == []


def test_price_of_a_copy_not_featured_is_rejected():
    # 37.99 is a real price, but copy 58 isn't featured in this answer.
    assert check('It costs $37.99.', featured=[57]) != []


def test_extra_amounts_from_the_state_are_allowed():
    msg = 'Within your $40 budget: this one is $38.'
    assert check(msg) != []
    assert check(msg, extra_amounts=[Decimal('40')]) == []


def test_no_amounts_no_featured_is_fine():
    assert check('Kind of Blue is a modal jazz classic.', featured=[]) == []


def test_stock_claim_about_unavailable_title_rejected():
    issues = check('We have Kind of Blue in great shape.', catalog_titles=CATALOG_TITLES)
    assert issues == ['"kind of blue" is described as available, but no copy of it is in stock']


def test_stock_claim_about_available_title_passes():
    assert check('We have Head Hunters for $38.', catalog_titles=CATALOG_TITLES) == []


def test_context_mention_without_claim_passes():
    msg = 'If you like Kind of Blue, try this one. Head Hunters is in stock for $38.'
    assert check(msg, catalog_titles=CATALOG_TITLES) == []


@pytest.mark.parametrize('honest', [
    "We don't have Kind of Blue in stock right now.",
    'Kind of Blue is not available at the moment.',
    'Sadly Kind of Blue is sold out.',
    "Kind of Blue isn't available, but Head Hunters is a great pick.",
])
def test_denying_stock_is_not_a_false_claim(honest):
    """Found in the first real graph run: "not available" was flagged as a stock claim."""
    assert check(honest, featured=[], catalog_titles=CATALOG_TITLES) == []


def test_title_found_despite_different_spelling():
    """Normalization at work: 'KIND OF BLUE' and 'Kind Of Blue' are the same title."""
    assert check('KIND OF BLUE is available today!', catalog_titles=CATALOG_TITLES) != []
