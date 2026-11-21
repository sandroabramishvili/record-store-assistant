"""Create a staff user, or promote an existing user to staff.

There is deliberately no API endpoint for this: anyone could make themselves staff.
Only someone with access to the server/database can run it.

Usage (from record-store-assistant/):
    python -m scripts.create_staff owner@store.com "Store Owner"

The password is asked interactively (hidden), so it never appears in the
terminal history or the process list.
"""

import argparse
import asyncio
import getpass

from sqlalchemy import select

from app.auth.security import hash_password
from app.db import SessionLocal, engine
from app.models import TasteProfile, User


async def main(email: str, full_name: str) -> None:
    email = email.strip().lower()
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == email))
        if user is not None:
            user.role = 'staff'
            await db.commit()
            print(f'Promoted existing user {email} to staff (id={user.id}).')
        else:
            password = getpass.getpass('Password (min 8 chars): ')
            if len(password) < 8 or password != getpass.getpass('Repeat password: '):
                raise SystemExit('Passwords too short or do not match. Nothing created.')
            user = User(email=email, full_name=full_name, hashed_password=hash_password(password), role='staff')
            user.profile = TasteProfile()
            db.add(user)
            await db.commit()
            print(f'Created staff user {email} (id={user.id}).')
    await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('email')
    parser.add_argument('full_name', nargs='?', default='Staff')
    args = parser.parse_args()
    asyncio.run(main(args.email, args.full_name))
