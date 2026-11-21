from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.deps import CurrentUser, DbSession
from app.auth.security import DUMMY_HASH, create_access_token, hash_password, verify_password
from app.models import TasteProfile, User
from app.schemas.user import Token, UserCreate, UserOut

router = APIRouter(prefix='/auth', tags=['auth'])


@router.post('/register', response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, db: DbSession) -> User:
    """Create a customer account plus an empty taste profile (one transaction)."""
    if await db.scalar(select(User.id).where(User.email == data.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, 'Email already registered')

    # role is never taken from the request: public sign-up is always a customer.
    user = User(
        email=data.email,
        full_name=data.full_name,
        hashed_password=hash_password(data.password),
        role='customer',
    )
    user.profile = TasteProfile()  # inserted together with the user
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Two simultaneous sign-ups with the same email: the unique index decides.
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, 'Email already registered')
    return user


@router.post('/login', response_model=Token)
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession) -> Token:
    """OAuth2 password flow: form fields `username` (= email) and `password`."""
    user = await db.scalar(select(User).where(User.email == form.username.strip().lower()))

    # Always run one Argon2 verify, so "unknown email" and "wrong password"
    # take the same time and return the same message.
    valid = verify_password(form.password, user.hashed_password if user else DUMMY_HASH)
    if user is None or not valid:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            'Incorrect email or password',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    return Token(access_token=create_access_token(user.id, user.role))


@router.get('/me', response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
