"""Shapes for My Collection (/collection) and the Wantlist (/wantlist)."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator

from app.schemas.common import Money, Name
from app.services.conditions import Condition

# ---------- collection ----------


class CollectionCreate(BaseModel):
    """Either `record_id` (from the catalog) or free-text `artist` + `title`, not both."""

    record_id: int | None = None
    artist: Name | None = None
    title: Name | None = None

    @model_validator(mode='after')
    def _one_way(self) -> 'CollectionCreate':
        free_text = self.artist is not None or self.title is not None
        if self.record_id is not None and free_text:
            raise ValueError('Send either record_id or artist + title, not both')
        if self.record_id is None and (self.artist is None or self.title is None):
            raise ValueError('Send record_id, or both artist and title')
        return self


class CollectionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    record_id: int | None
    artist: str
    title: str
    added_at: datetime


class CollectionStats(BaseModel):
    total: int
    in_catalog: int  # genre/decade are only known for records linked to the catalog
    by_genre: dict[str, int]
    by_decade: dict[str, int]  # "1970s": 4


class CollectionOut(BaseModel):
    items: list[CollectionItemOut]
    stats: CollectionStats


# ---------- wantlist ----------


class WantlistCreate(BaseModel):
    artist: Name
    title: Name | None = None  # None = anything by this artist
    max_price: Money | None = None
    min_condition: Condition | None = None


class WantlistItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    artist: str
    title: str | None
    max_price: Decimal | None
    min_condition: str | None
    matched_item_id: int | None
    created_at: datetime


class WantlistOut(BaseModel):
    items: list[WantlistItemOut]
    match_count: int  # for the "1 wantlist match!" badge in the UI
