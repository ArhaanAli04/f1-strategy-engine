"""Integration-test fixtures: real Postgres + Redis, spun up fresh via testcontainers."""

import asyncio
import os
from collections.abc import Generator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

import backend.core.database as database_module
import backend.models  # noqa: F401 — registers all tables on Base.metadata
from backend.core.config import get_db_settings, get_redis_settings
from backend.core.database import Base


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    # Plain postgres, not the timescale/timescaledb image docker-compose uses —
    # no table in the current schema is a hypertable yet (see CLAUDE.md's note
    # on lap_data), so the extension isn't needed for these tests.
    with PostgresContainer("postgres:15-alpine", driver="asyncpg") as container:
        yield container


@pytest.fixture(scope="session")
def redis_container() -> Generator[RedisContainer, None, None]:
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture(scope="session", autouse=True)
def _point_settings_at_containers(
    postgres_container: PostgresContainer, redis_container: RedisContainer
) -> None:
    """Redirect DATABASE_URL/REDIS_URL at the ephemeral containers, not the real .env.

    Settings and the DB engine are cached as module-level singletons (via
    @lru_cache / a plain global) so the app never reconnects mid-process.
    Integration tests must force a fresh connection to the container instead
    of whatever DATABASE_URL/REDIS_URL is configured for local dev.
    """
    db_url = postgres_container.get_connection_url()
    redis_url = (
        f"redis://{redis_container.get_container_host_ip()}:"
        f"{redis_container.get_exposed_port(6379)}"
    )

    os.environ["DATABASE_URL"] = db_url
    os.environ["TIMESCALE_URL"] = db_url
    os.environ["REDIS_URL"] = redis_url
    os.environ.setdefault("SECRET_KEY", "test-secret-key")

    get_db_settings.cache_clear()
    get_redis_settings.cache_clear()
    database_module._engine = None
    database_module._session_factory = None


@pytest.fixture
def db_session_factory() -> Generator[async_sessionmaker[AsyncSession], None, None]:
    """An async_sessionmaker against the containerized Postgres, schema created fresh.

    Returns a factory rather than a live session: tests using this run plain
    (sync) test functions and open their own session per asyncio.run() call,
    matching exactly how each Celery task under test opens its own session —
    real Celery workers never have an event loop already running in their
    thread, and this fixture must not either, or asyncio.run() inside a task
    invoked eagerly from the test would fail with "cannot be called from a
    running event loop".
    """
    engine = database_module.get_engine()

    async def _create_schema() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Every separate asyncio.run() call gets its own event loop, but
        # get_engine()'s pooled asyncpg connections are bound to whichever
        # loop created them — dispose so nothing pooled here survives into
        # the test's own (separately asyncio.run()'d) session usage.
        await engine.dispose()

    asyncio.run(_create_schema())

    yield async_sessionmaker(engine, expire_on_commit=False)

    async def _drop_schema() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    asyncio.run(_drop_schema())
