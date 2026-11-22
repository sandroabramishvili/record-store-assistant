"""Reservation constants and errors. Pure (no DB), so the graph nodes can import them.

Plain exceptions, not HTTPException: the same service is used by the API (→ 409/404)
and by the chat graph (→ a friendly sentence). Each caller translates them.
"""

HOLD_HOURS = 48
MAX_ACTIVE = 3


class ReservationError(Exception):
    """Base class. `str(error)` is a message that can be shown to the customer."""


class ReservationConflict(ReservationError):
    def __init__(self) -> None:
        super().__init__('Sorry, someone just reserved that copy (or it was sold).')


class ReservationLimitReached(ReservationError):
    def __init__(self) -> None:
        super().__init__(f'You already have {MAX_ACTIVE} active reservations. '
                         'Pick one up or cancel one to reserve another.')


class ItemNotFound(ReservationError):
    def __init__(self) -> None:
        super().__init__('That copy does not exist.')


class ReservationNotActive(ReservationError):
    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f'This reservation is already {status}.')
