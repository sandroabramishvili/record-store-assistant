"""Goldmine grading scale, worst → best. Single source of truth for condition grades."""

from typing import Literal, get_args

# Used as a Pydantic type: only these strings are accepted (422 otherwise).
Condition = Literal['P', 'F', 'G', 'G+', 'VG', 'VG+', 'NM', 'M']

CONDITION_ORDER: list[str] = list(get_args(Condition))


def condition_rank(grade: str) -> int:
    """Position on the scale: P = 0 ... M = 7. Raises ValueError for an unknown grade."""
    return CONDITION_ORDER.index(grade)


def meets_minimum(grade: str, minimum: str | None) -> bool:
    """True if `grade` is at least `minimum` (no minimum → always True)."""
    return minimum is None or condition_rank(grade) >= condition_rank(minimum)
