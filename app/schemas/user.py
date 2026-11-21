"""Request/response shapes for auth and the taste profile.

Schemas are the API's contract: what the client may send (validated → 422 if wrong)
and what it gets back (never `hashed_password`).
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, model_validator

from app.services.conditions import Condition

# ---------- auth ----------


def _lower(value: str) -> str:
    return value.strip().lower()


Email = Annotated[EmailStr, AfterValidator(_lower)]


class UserCreate(BaseModel):
    email: Email
    full_name: str = Field(min_length=1, max_length=150)
    # max_length: Argon2 hashing a 1 MB "password" would be a cheap way to burn CPU.
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # build from a SQLAlchemy object

    id: int
    email: str
    full_name: str
    role: Literal['customer', 'staff']
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = 'bearer'


# ---------- taste profile ----------


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

# Money: exact Decimal, max 999999.99, always 2 decimals ("40" → "40.00", like the DB).
# Serialized as a JSON string to keep it exact.
Money = Annotated[
    Decimal,
    Field(gt=0, max_digits=8, decimal_places=2),
    AfterValidator(lambda d: d.quantize(Decimal('0.01'))),
]


class ProfileBase(BaseModel):
    favorite_genres: GenreList = []
    disliked_genres: GenreList = []
    budget_cap: Money | None = None
    min_condition: Condition | None = None


class ProfileUpdate(ProfileBase):
    """PUT = full replacement: a field left out goes back to its default."""

    @model_validator(mode='after')
    def _no_overlap(self) -> 'ProfileUpdate':
        both = set(self.favorite_genres) & set(self.disliked_genres)
        if both:
            raise ValueError(f'Genres cannot be both favorite and disliked: {", ".join(sorted(both))}')
        return self


class ProfileOut(ProfileBase):
    model_config = ConfigDict(from_attributes=True)
