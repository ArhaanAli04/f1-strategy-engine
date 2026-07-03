"""Shared unit-test fixtures: mocks only — no real DB or Redis touched here.

Integration-test fixtures (real Postgres + Redis via testcontainers) live in
backend/tests/integration/conftest.py instead, since they're heavier and
scoped only to that test tier.
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import fakeredis as fakeredis_lib
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """A spec'd AsyncSession mock — auto-detects async vs sync methods from the spec."""
    return AsyncMock(spec=AsyncSession)


@pytest_asyncio.fixture
async def fakeredis() -> AsyncGenerator[fakeredis_lib.FakeAsyncRedis, None]:
    """An in-memory fakeredis client standing in for a real Redis connection."""
    client = fakeredis_lib.FakeAsyncRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()  # type: ignore[attr-defined]
