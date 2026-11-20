"""FastAPI dependencies for authentication (who are you?) and roles (may you?).

Usage in a router:

    @router.get('/auth/me')
    async def me(user: CurrentUser): ...

    @router.post('/inventory')
    async def add_item(staff: StaffUser, db: DbSession): ...
"""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_access_token
from app.db import get_db
from app.models import User

# Reads `Authorization: Bearer <token>` from the request (401 if missing).
# `tokenUrl` tells Swagger's "Authorize" button where to log in.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/auth/login')

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _unauthorized(detail: str) -> HTTPException:
    # The WWW-Authenticate header is what the OAuth2/HTTP spec expects with a 401.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={'WWW-Authenticate': 'Bearer'},
    )


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DbSession,
) -> User:
    """Valid token + existing user → the User. Otherwise 401."""
    try:
        payload = decode_access_token(token)
        user_id = int(payload['sub'])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise _unauthorized('Invalid or expired token')

    # Always reload from the DB: the token's `role` claim is not trusted,
    # so role changes and deleted accounts take effect immediately.
    user = await db.get(User, user_id)
    if user is None:
        raise _unauthorized('User not found')
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_staff(user: CurrentUser) -> User:
    """Logged in but not staff → 403."""
    if user.role != 'staff':
        raise HTTPException(status.HTTP_403_FORBIDDEN, 'Staff only')
    return user


StaffUser = Annotated[User, Depends(require_staff)]
