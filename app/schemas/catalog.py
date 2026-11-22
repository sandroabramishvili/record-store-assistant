"""Shapes for releases (/records) and physical copies (/inventory)."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.schemas.common import GenreList, Money, Name
from app.services.conditions import Condition

Format = Literal['LP', 'EP', '7"', '10"', '12"']
Year = Annotated[int, Field(ge=1900, le=2100)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]


class Track(BaseModel):
    position: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10)]  # "A1"
    title: Name
    duration: Annotated[str, StringConstraints(pattern=r'^\d{1,2}:\d{2}$')] | None = None  # "5:32"


# ---------- releases ----------


class RecordIn(BaseModel):
    """Create (POST) and full replace (PUT). `is_reissue` and `*_norm` are computed, never sent."""

    artist: Name
    title: Name
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    catalog_number: ShortText | None = None
    release_year: Year
    original_year: Year
    country: ShortText | None = None
    format: Format = 'LP'
    genres: GenreList = []
    styles: GenreList = []
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)]
    tracklist: Annotated[list[Track], Field(max_length=60)] = []

    @model_validator(mode='after')
    def _years(self) -> 'RecordIn':
        if self.original_year > self.release_year:
            raise ValueError('original_year cannot be after release_year')
        return self


class RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    artist: str
    title: str
    label: str
    catalog_number: str | None
    release_year: int
    original_year: int
    country: str | None
    format: str
    is_reissue: bool
    genres: list[str]
    styles: list[str]


class RecordSummary(RecordOut):
    """A row in the catalog list: the release plus what's available right now."""

    available_copies: int
    lowest_price: Decimal | None


# ---------- copies ----------


class InventoryIn(BaseModel):
    """Fields staff can edit on a copy (PUT). Status changes go through PATCH."""

    media_condition: Condition
    sleeve_condition: Condition
    price: Money
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None


class InventoryCreate(InventoryIn):
    record_id: int


class StatusUpdate(BaseModel):
    # 'reserved' is not allowed here: reservations are created only by the reservation service.
    status: Literal['in_stock', 'sold']


class InventoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    record_id: int
    media_condition: str
    sleeve_condition: str
    price: Decimal
    status: str
    notes: str | None
    added_at: datetime


class RecordDetail(RecordOut):
    description: str
    tracklist: list[Track]
    created_at: datetime
    copies: list[InventoryOut]
