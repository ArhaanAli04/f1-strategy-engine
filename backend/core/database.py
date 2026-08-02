from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        from backend.core.config import get_db_settings

        settings = get_db_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            # Supabase's app-runtime URL goes through PgBouncer in
            # transaction-pooling mode, which does not support asyncpg's
            # default prepared-statement caching — a pooled connection can be
            # handed to a different session mid-cache, raising
            # DuplicatePreparedStatementError (confirmed Day 24: hit on every
            # pod at startup, intermittently persisting on some). Harmless to
            # disable against docker-compose's local Postgres too (no pooler
            # in front of it), so this is unconditional rather than branching
            # on environment.
            connect_args={"statement_cache_size": 0},
        )
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an AsyncSession per request."""
    async with _get_session_factory()() as session:
        yield session
