from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import Base

_engine: AsyncEngine | None = None
SessionLocal: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, SessionLocal
    if _engine is None:
        settings = get_settings()
        kwargs: dict = {"echo": False, "future": True}
        if not settings.is_sqlite:
            kwargs["pool_pre_ping"] = True
        _engine = create_async_engine(settings.database_url, **kwargs)
        SessionLocal = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert SessionLocal is not None
    return SessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_models() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_models() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
