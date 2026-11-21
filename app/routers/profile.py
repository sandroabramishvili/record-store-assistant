from fastapi import APIRouter

from app.auth.deps import CurrentUser, DbSession
from app.models import TasteProfile, User
from app.schemas.user import ProfileOut, ProfileUpdate

router = APIRouter(prefix='/profile', tags=['profile'])


async def _get_or_create_profile(db: DbSession, user: User) -> TasteProfile:
    """Every user should have a profile from registration; create one if it's missing
    (e.g. a staff user made before this existed). Never 404 on your own profile."""
    profile = await db.get(TasteProfile, user.id)
    if profile is None:
        profile = TasteProfile(user_id=user.id, favorite_genres=[], disliked_genres=[])
        db.add(profile)
        await db.flush()
    return profile


@router.get('', response_model=ProfileOut)
async def get_profile(user: CurrentUser, db: DbSession) -> TasteProfile:
    profile = await _get_or_create_profile(db, user)
    await db.commit()
    return profile


@router.put('', response_model=ProfileOut)
async def update_profile(data: ProfileUpdate, user: CurrentUser, db: DbSession) -> TasteProfile:
    """Full replacement. The only way to change the profile — never through the chat."""
    profile = await _get_or_create_profile(db, user)
    for field, value in data.model_dump().items():
        setattr(profile, field, value)
    await db.commit()
    return profile
