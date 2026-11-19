from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# One engine for the whole application. It owns the connection pool.
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,   # check a pooled connection is alive before using it
)

# Factory that creates a new AsyncSession each time it is called.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# Predictable names for indexes and constraints, so Alembic migrations
# can find them later (e.g. when dropping or altering a constraint).
NAMING_CONVENTION = {
    'ix': 'ix_%(column_0_label)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}


class Base(DeclarativeBase):
    """Parent class for all SQLAlchemy models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, closed automatically afterwards."""
    async with SessionLocal() as session:
        yield session
