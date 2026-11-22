from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class Count(BaseModel):
    name: str
    count: int


class Share(Count):
    share: float                       # of all requests in the window, 0..1


class DemandOut(BaseModel):
    days: int
    searches: int
    genres: list[Count]
    artists: list[Count]
    titles: list[Count]
    decades: list[Count]


class UnmetSearch(BaseModel):
    at: datetime
    relax_attempts: int
    asked: dict                        # the constraints as the customer asked (never the relaxed ones)


class WantedRecord(BaseModel):
    artist: str
    title: str | None                  # None = anything by this artist
    customers: int
    highest_max_price: Decimal | None


class UnmetDemandOut(BaseModel):
    days: int
    searches: int
    zero_result_searches: int
    zero_result_rate: float | None     # None when there were no searches
    genres: list[Count]
    artists: list[Count]
    titles: list[Count]
    recent: list[UnmetSearch]
    most_wanted: list[WantedRecord]    # open wantlist entries (current, not windowed)


class ReservationStatsOut(BaseModel):
    days: int
    created: int
    active: int
    completed: int
    cancelled: int
    expired: int
    conversion_rate: float | None      # completed / created
    completion_rate_of_closed: float | None   # completed / (completed + cancelled + expired)


class IntentsOut(BaseModel):
    days: int
    total: int
    intents: list[Share]
