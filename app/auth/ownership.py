"""Object-level authorization: may this user touch this particular row?

Role checks (`require_staff`) answer "may you use this endpoint?".
This answers "is this chat / collection item / reservation yours?".
"""

from collections.abc import Sequence
from typing import Any, TypeVar

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User

T = TypeVar('T')


async def get_owned_or_404(
    db: AsyncSession,
    model: type[T],
    obj_id: int,
    user: User,
    *,
    allow_staff: bool = False,
    options: Sequence[Any] = (),
) -> T:
    """Load `model` by primary key; return it only if `user` owns it.

    - Missing **or** someone else's → the same 404, so a stranger can't tell
      whether the id exists.
    - `allow_staff=True`: staff may access any user's object
      (e.g. cancelling a reservation).
    - `options`: loader options such as `selectinload(...)`, since
      relationships are `lazy='raise'`.

    The model must have a `user_id` column.
    """
    obj = await db.get(model, obj_id, options=options)
    if obj is None:
        raise _not_found()
    if allow_staff and user.role == 'staff':
        return obj
    if obj.user_id != user.id:
        raise _not_found()
    return obj


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, 'Not found')
