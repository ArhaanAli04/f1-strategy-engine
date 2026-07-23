"""Alembic migration integration tests: run the real migration scripts end-to-end
against a fresh TimescaleDB container.

Deliberately does NOT use db_session_factory (backend/tests/integration/conftest.py),
which creates schema via Base.metadata.create_all/drop_all — that bypasses Alembic
entirely. These tests exercise the actual revision chain under
backend/migrations/versions/, which is the only way to catch a broken revision, a
bad downgrade, or drift between the ORM models and what the migrations actually
produce against a real database.

Each test resets the database to a truly blank state (DROP SCHEMA public CASCADE)
before and after running, rather than relying on `alembic downgrade base` on a
database that might carry state left behind by another test file's
db_session_factory-based Base.metadata.create_all — the two schema-creation paths
are independent and must not assume anything about each other's cleanup.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from backend.core.config import get_db_settings

_REPO_ROOT = Path(__file__).resolve().parents[3]

_EXPECTED_TABLES = {
    "circuits",
    "races",
    "sessions",
    "drivers",
    "teams",
    "driver_contracts",
    "lap_data",
    "tire_stints",
    "sector_times",
    "strategy_predictions",
    "pit_events",
    "users",
    "alerts",
    "subscriptions",
    "alembic_version",
}


def _alembic_config() -> Config:
    """A Config pointed at the real alembic.ini — env.py resolves the DB URL
    itself via get_db_settings().database_url (see migrations/env.py's
    get_url()), which reads whatever DATABASE_URL _point_settings_at_containers
    (session-scoped autouse, conftest.py) has already redirected at the
    container, so no URL needs to be set on this Config directly.
    """
    return Config(str(_REPO_ROOT / "alembic.ini"))


async def _reset_to_blank_database() -> None:
    """Drop and recreate the public schema — a truly blank database, no
    alembic_version table, no leftover tables from any other test path.
    """
    engine = create_async_engine(get_db_settings().database_url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


async def _fetch_table_names() -> set[str]:
    engine = create_async_engine(get_db_settings().database_url)
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        )
        names = {row[0] for row in result}
    await engine.dispose()
    return names


async def _fetch_schema_fingerprint() -> set[tuple[str, str, str, str]]:
    """(table_name, column_name, data_type, is_nullable) for every column in
    the public schema — a strong enough fingerprint to catch a downgrade that
    doesn't fully undo its upgrade, without being as brittle as a full DDL diff.
    """
    engine = create_async_engine(get_db_settings().database_url)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name, column_name, data_type, is_nullable "
                "FROM information_schema.columns WHERE table_schema = 'public'"
            )
        )
        fingerprint = {(row[0], row[1], row[2], row[3]) for row in result}
    await engine.dispose()
    return fingerprint


@pytest.fixture
def _blank_database(postgres_container: PostgresContainer) -> Any:
    """Ensure a blank database before the test, and leave one behind after —
    postgres_container is session-scoped (shared with every other integration
    test file), so both ends of this fixture matter, not just setup.
    """
    asyncio.run(_reset_to_blank_database())
    yield
    asyncio.run(_reset_to_blank_database())


@pytest.mark.integration
@pytest.mark.usefixtures("_blank_database")
def test_upgrade_from_base_to_head_succeeds() -> None:
    """Every migration in the revision chain applies cleanly, in order, with no errors."""
    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    table_names = asyncio.run(_fetch_table_names())
    assert _EXPECTED_TABLES <= table_names


@pytest.mark.integration
@pytest.mark.usefixtures("_blank_database")
def test_downgrade_and_upgrade_is_idempotent() -> None:
    """upgrade -> downgrade to base -> upgrade again produces the identical schema."""
    cfg = _alembic_config()

    command.upgrade(cfg, "head")
    first_fingerprint = asyncio.run(_fetch_schema_fingerprint())

    command.downgrade(cfg, "base")
    table_names_after_downgrade = asyncio.run(_fetch_table_names())
    # alembic_version itself is never dropped by any migration's downgrade()
    # (Alembic manages that table outside the revision chain) — everything
    # else must be gone.
    assert table_names_after_downgrade <= {"alembic_version"}

    command.upgrade(cfg, "head")
    second_fingerprint = asyncio.run(_fetch_schema_fingerprint())

    assert first_fingerprint == second_fingerprint


@pytest.mark.integration
@pytest.mark.usefixtures("_blank_database")
def test_all_tables_created() -> None:
    """After a full upgrade, every table declared across the ORM models exists."""
    cfg = _alembic_config()
    command.upgrade(cfg, "head")

    table_names = asyncio.run(_fetch_table_names())
    missing = _EXPECTED_TABLES - table_names
    assert not missing, f"Missing tables after upgrade: {missing}"
