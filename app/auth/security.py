"""Password hashing and JWT access tokens.

Pure functions, no database and no FastAPI: easy to unit-test.
"""

from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.config import settings

ALGORITHM = 'HS256'

# Argon2id with pwdlib's recommended parameters.
password_hash = PasswordHash.recommended()

# Used by login when the email doesn't exist: we still run one verify against
# this hash, so "unknown email" and "wrong password" take the same time.
DUMMY_HASH = password_hash.hash('dummy-password-for-timing')


def hash_password(password: str) -> str:
    """Return an Argon2 hash (salt and parameters are stored inside the string)."""
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """True if `password` matches `hashed`."""
    return password_hash.verify(password, hashed)


def create_access_token(user_id: int, role: str) -> str:
    """Signed token: who the user is (`sub`), their role, when it was issued and expires."""
    now = datetime.now(timezone.utc)
    payload = {
        'sub': str(user_id),  # JWT spec: `sub` must be a string
        'role': role,
        'iat': now,
        'exp': now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Verify signature and expiry and return the payload.

    Raises `jwt.PyJWTError` (e.g. `ExpiredSignatureError`, `InvalidSignatureError`)
    if the token is invalid. The caller turns that into a 401.
    """
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[ALGORITHM],  # a fixed list: never trust the algorithm named in the token
        options={'require': ['sub', 'exp']},
    )
