from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, StringConstraints

from app.schemas.common import Name


class SessionCreate(BaseModel):
    """Optional title; if left out, the first message becomes the title."""

    title: Name | None = None


class SessionOut(BaseModel):
    model_config = {'from_attributes': True}

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


MAX_MESSAGE_CHARS = 1000   # spec 11 cost control: longer → 422 before anything runs


class MessageIn(BaseModel):
    """One customer message. The user and the thread come from the token and the URL, never the body."""

    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS)]


class ConfirmIn(BaseModel):
    """The answer to a confirmation_required event (spec 7.7)."""

    approved: bool


class ChatItem(BaseModel):
    """A copy shown in a message, as a card. `status` is the CURRENT status (it may be sold since)."""

    id: int
    artist: str
    title: str
    year: int | None
    pressing: str                     # "original" or "2016 reissue"
    media_condition: str
    sleeve_condition: str
    price: Decimal
    status: str


class MessageOut(BaseModel):
    id: int
    role: Literal['user', 'assistant']
    content: str
    items: list[ChatItem] = []
    created_at: datetime


class ChatHistory(BaseModel):
    """Everything the UI needs to redraw a chat, including an unanswered confirmation question."""

    session: SessionOut
    messages: list[MessageOut]
    pending_confirmation: dict[str, Any] | None = None
