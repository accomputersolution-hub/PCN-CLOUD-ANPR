from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.exceptions import ValidationAppError
from app.core.providers import normalize_datastore_provider
from app.db.base import Base

_engine: AsyncEngine | None = None
SessionLocal: async_sessionmaker[AsyncSession] | None = None


def sqlalchemy_enabled() -> bool:
    return normalize_datastore_provider(get_settings().datastore_provider) == "sqlalchemy"


def get_engine() -> AsyncEngine:
    global _engine, SessionLocal
    if not sqlalchemy_enabled():
        raise ValidationAppError(
            "DATABASE_URL / SQLAlchemy engine is not used when DATASTORE_PROVIDER=firestore",
        )
    if _engine is None:
        settings = get_settings()
        if not (settings.database_url or "").strip():
            raise ValidationAppError(
                "DATABASE_URL is required when DATASTORE_PROVIDER=sqlalchemy",
            )
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


async def get_db() -> AsyncGenerator[AsyncSession | None, None]:
    if not sqlalchemy_enabled():
        yield None
        return
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_models() -> None:
    if not sqlalchemy_enabled():
        return
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_models() -> None:
    if not sqlalchemy_enabled():
        return
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
