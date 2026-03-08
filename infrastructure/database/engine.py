"""Async SQLAlchemy engine + session factory."""
import os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base

try:
    from sqlalchemy.ext.asyncio import async_sessionmaker
    def _make_session_factory(engine):
        return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
except ImportError:
    from sqlalchemy.orm import sessionmaker
    def _make_session_factory(engine):
        return sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

from settings import settings

DATABASE_URL = settings.DATABASE_URL
DATABASE_URL = (
    DATABASE_URL
    .replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    .replace("postgresql://", "postgresql+asyncpg://")
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = _make_session_factory(engine)

Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
