from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

ReservationStatus = Literal['active', 'completed', 'cancelled', 'expired']


class ReservationCreate(BaseModel):
    """Only the copy. The customer comes from the token, never from the body."""

    inventory_item_id: int


class ReservationOut(BaseModel):
    """A reservation plus what the UI needs to show it as a card."""

    id: int
    user_id: int
    status: ReservationStatus
    expires_at: datetime
    created_at: datetime
    # the copy
    inventory_item_id: int
    price: Decimal
    media_condition: str
    sleeve_condition: str
    # the release
    record_id: int
    artist: str
    title: str
