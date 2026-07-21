"""Integration-test fixtures: real Postgres + Redis, spun up fresh via testcontainers."""

import asyncio
import os
import uuid
from collections.abc import Generator

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

import backend.core.database as database_module
import backend.models  # noqa: F401 — registers all tables on Base.metadata
from backend.core.config import get_db_settings, get_redis_settings
from backend.core.database import Base


@pytest.fixture(scope="session")
def postgres_container() -> Generator[PostgresContainer, None, None]:
    # Same image docker-compose.yml uses (see CLAUDE.md's TimescaleDB note —
    # lap_data isn't a hypertable yet, but Day 16's Alembic migration tests
    # must run against the real image, not a plain-postgres stand-in, since
    # migration b2e4f6a8c0d1 installs the extension itself.
    with PostgresContainer("timescale/timescaledb:latest-pg15", driver="asyncpg") as container:
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


@pytest.fixture(autouse=True)
def _flush_redis_between_tests(redis_container: RedisContainer) -> None:
    """Flush db 0 of the shared session-scoped Redis container before every test.

    Both cache_service's cache keys and slowapi's rate-limit counters
    (core/rate_limit.py's Limiter, storage_uri=REDIS_URL) live in db 0 of the
    one Redis container shared across the whole test session. Without this,
    state accumulates across tests — most importantly slowapi's per-bucket
    counters: Starlette's TestClient always reports client.host ==
    "testclient", so every unauthenticated request across every test in the
    session would otherwise share one "ip:testclient" bucket capped at
    10/minute, tripping 429s unrelated to what any individual test checks.
    """
    client = sync_redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
    )
    try:
        client.flushdb()
    finally:
        client.close()


@pytest.fixture
def test_client(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> Generator[TestClient, None, None]:
    """FastAPI TestClient wired to the real containerized Postgres + Redis.

    db_session_factory is depended on (not used directly) purely to force
    schema creation before any request hits the app — see its own docstring.

    backend.main is imported here, inside the fixture body, rather than at
    module level: core/rate_limit.py's `limiter = Limiter(...,
    storage_uri=get_redis_settings().redis_url)` is a module-level singleton
    evaluated at first import, and must not bind to the real .env REDIS_URL
    instead of the container's. A module-level `from backend.main import app`
    would import it at collection time, before the session-scoped
    _point_settings_at_containers fixture (which redirects REDIS_URL) has run.
    """
    from backend.main import app

    with TestClient(app) as client:
        yield client


@pytest.fixture
def authenticated_client(test_client: TestClient) -> TestClient:
    """test_client with a valid access token pre-set as the Authorization header.

    Registers a throwaway user and logs in through the real HTTP auth flow
    (register -> login), not a shortcut through user_service directly, so
    this exercises the same path a real client goes through to obtain a
    bearer token.
    """
    email = f"integration-{uuid.uuid4()}@example.com"
    password = "IntegrationTest123!"  # noqa: S105
    test_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Integration Test User"},
    )
    login_response = test_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    access_token = login_response.json()["access_token"]
    test_client.headers.update({"Authorization": f"Bearer {access_token}"})
    return test_client


async def _add_and_commit(
    db_session_factory: async_sessionmaker[AsyncSession], *rows: object
) -> None:
    async with db_session_factory() as db:
        db.add_all(rows)
        await db.commit()


def seed_via_test_client(
    test_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession], *rows: object
) -> None:
    """Seed ORM rows on test_client's own event loop, not a fresh asyncio.run() loop.

    test_client's lifespan startup (main.py's DB/Redis health checks) already
    opened a pooled asyncpg connection bound to TestClient's persistent anyio
    portal loop — get_engine() is a shared singleton, so a separate
    asyncio.run() call from a test body would create a second, different
    loop, and the pool could hand that call one of the portal loop's
    connections, triggering an asyncpg cross-loop RuntimeError (confirmed
    while writing test_race_api.py). Running via test_client.portal.call
    keeps every DB access on the one loop TestClient already owns for its
    lifetime — the same loop its own .get()/.post() methods already use.
    """
    test_client.portal.call(_add_and_commit, db_session_factory, *rows)  # type: ignore[union-attr]
