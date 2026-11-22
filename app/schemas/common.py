"""Reusable field types and the pagination wrapper, shared by all schemas."""

from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints

# Money: exact Decimal, max 999999.99, always 2 decimals ("40" → "40.00", like the DB).
# Serialized as a JSON string to keep it exact.
Money = Annotated[
    Decimal,
    Field(gt=0, max_digits=8, decimal_places=2),
    AfterValidator(lambda d: d.quantize(Decimal('0.01'))),
]


def _clean_genres(values: list[str]) -> list[str]:
    """Lower-case, trim, drop empties and duplicates (keeping order)."""
    seen: dict[str, None] = {}
    for v in values:
        v = ' '.join(v.split()).lower()
        if v:
            seen.setdefault(v, None)
    return list(seen)


Genre = Annotated[str, Field(max_length=50)]
GenreList = Annotated[list[Genre], Field(max_length=20), AfterValidator(_clean_genres)]

# Non-empty text after trimming (strip happens BEFORE the length check).
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class Page[T](BaseModel):
    """One page of a list: the items plus what the UI needs to draw page controls."""

    items: list[T]
    total: int
    page: int
    size: int
