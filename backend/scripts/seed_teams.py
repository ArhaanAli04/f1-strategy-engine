"""Seed the current 2026 F1 season roster: teams, missing drivers, and contracts.

Historical ingestion (2018-2025, see CLAUDE.md's Data Quality Notes) already
populated the drivers table from FastF1 session data, but current-season
rookies with no prior FastF1 session (e.g. Arvid Lindblad, promoted straight
to a 2026 race seat) never got a row. This script creates any missing driver
first, then links every 2026 grid driver to their team via driver_contracts —
the same "create Team/Driver rows if missing, then the contract" order
covers both upstream tables, not just drivers.

Grid confirmed 2026-07-18 (11 teams, including the new Cadillac entry).
Run via: make seed-teams
"""

import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.driver import Driver, DriverContract, Team

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CONTRACT_SEASON = 2026

# (constructor_id, name, color_hex)
# Colors are best-effort approximations, not official hex values — Audi and
# Cadillac in particular have never raced, so their liveries are a guess.
_TEAMS: list[tuple[str, str, str]] = [
    ("mclaren", "McLaren", "#FF8000"),
    ("ferrari", "Ferrari", "#E8002D"),
    ("red_bull", "Red Bull Racing", "#3671C6"),
    ("mercedes", "Mercedes", "#27F4D2"),
    ("williams", "Williams", "#64C4FF"),
    ("audi", "Audi", "#BB0A30"),
    ("aston_martin", "Aston Martin", "#229971"),
    ("alpine", "Alpine", "#0090FF"),
    ("haas", "Haas", "#B6BABD"),
    ("racing_bulls", "Racing Bulls", "#6692FF"),
    ("cadillac", "Cadillac", "#8A8D8F"),
]

# (code, full_name, nationality) — full roster info, so any code missing from
# the drivers table (not just Lindblad) can be created on the fly, keeping
# this script self-contained against a fresh or partially-seeded DB.
_DRIVERS: dict[str, tuple[str, str]] = {
    "NOR": ("Lando Norris", ""),
    "PIA": ("Oscar Piastri", ""),
    "HAM": ("Lewis Hamilton", ""),
    "LEC": ("Charles Leclerc", ""),
    "VER": ("Max Verstappen", ""),
    "HAD": ("Isack Hadjar", ""),
    "RUS": ("George Russell", ""),
    "ANT": ("Andrea Kimi Antonelli", ""),
    "ALB": ("Alexander Albon", ""),
    "SAI": ("Carlos Sainz", ""),
    "HUL": ("Nico Hulkenberg", ""),
    "BOR": ("Gabriel Bortoleto", ""),
    "ALO": ("Fernando Alonso", ""),
    "STR": ("Lance Stroll", ""),
    "GAS": ("Pierre Gasly", ""),
    "COL": ("Franco Colapinto", ""),
    "OCO": ("Esteban Ocon", ""),
    "BEA": ("Oliver Bearman", ""),
    "LAW": ("Liam Lawson", ""),
    "LIN": ("Arvid Lindblad", "GBR"),
    "PER": ("Sergio Perez", "MEX"),
    "BOT": ("Valtteri Bottas", "FIN"),
}

# (driver_code, constructor_id)
_CONTRACTS: list[tuple[str, str]] = [
    ("NOR", "mclaren"),
    ("PIA", "mclaren"),
    ("HAM", "ferrari"),
    ("LEC", "ferrari"),
    ("VER", "red_bull"),
    ("HAD", "red_bull"),
    ("RUS", "mercedes"),
    ("ANT", "mercedes"),
    ("ALB", "williams"),
    ("SAI", "williams"),
    ("HUL", "audi"),
    ("BOR", "audi"),
    ("ALO", "aston_martin"),
    ("STR", "aston_martin"),
    ("GAS", "alpine"),
    ("COL", "alpine"),
    ("OCO", "haas"),
    ("BEA", "haas"),
    ("LAW", "racing_bulls"),
    ("LIN", "racing_bulls"),
    ("PER", "cadillac"),
    ("BOT", "cadillac"),
]


async def _ensure_teams(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Insert any missing team, return constructor_id -> id for every roster team."""
    existing_rows = (await session.execute(select(Team.constructor_id, Team.id))).all()
    ids: dict[str, uuid.UUID] = {row[0]: row[1] for row in existing_rows}

    new_teams = [
        Team(id=uuid.uuid4(), name=name, constructor_id=constructor_id, color_hex=color_hex)
        for constructor_id, name, color_hex in _TEAMS
        if constructor_id not in ids
    ]
    if new_teams:
        session.add_all(new_teams)
        await session.flush()
        ids.update({team.constructor_id: team.id for team in new_teams})
        logger.info("Inserted %d team(s)", len(new_teams))
    else:
        logger.info("Nothing to insert — all %d teams already present", len(_TEAMS))

    return ids


async def _ensure_drivers(session: AsyncSession) -> dict[str, uuid.UUID]:
    """Insert any missing driver, return code -> id for every roster driver."""
    existing_rows = (await session.execute(select(Driver.code, Driver.id))).all()
    ids: dict[str, uuid.UUID] = {row[0]: row[1] for row in existing_rows}

    new_drivers = [
        Driver(id=uuid.uuid4(), code=code, full_name=full_name, nationality=nationality)
        for code, (full_name, nationality) in _DRIVERS.items()
        if code not in ids
    ]
    if new_drivers:
        session.add_all(new_drivers)
        await session.flush()
        ids.update({driver.code: driver.id for driver in new_drivers})
        logger.info(
            "Inserted %d driver(s): %s",
            len(new_drivers),
            ", ".join(sorted(d.code for d in new_drivers)),
        )
    else:
        logger.info("Nothing to insert — all %d roster drivers already present", len(_DRIVERS))

    return ids


async def _ensure_contracts(
    session: AsyncSession,
    driver_ids: dict[str, uuid.UUID],
    team_ids: dict[str, uuid.UUID],
) -> None:
    """Insert any missing (driver, season) contract, skipping ones that already exist.

    driver_contracts has no DB-level unique constraint on (driver_id, season)
    (see models/driver.py), so duplicate-avoidance is done here at the
    application level, same convention as seed_circuits.py's skip-by-name set.
    """
    existing_pairs = {
        (driver_id, season)
        for driver_id, season in (
            await session.execute(select(DriverContract.driver_id, DriverContract.season))
        ).all()
    }

    new_contracts = [
        DriverContract(
            id=uuid.uuid4(),
            driver_id=driver_ids[driver_code],
            team_id=team_ids[constructor_id],
            season=CONTRACT_SEASON,
        )
        for driver_code, constructor_id in _CONTRACTS
        if (driver_ids[driver_code], CONTRACT_SEASON) not in existing_pairs
    ]
    if new_contracts:
        session.add_all(new_contracts)
        logger.info(
            "Inserted %d driver contract(s) for season %d", len(new_contracts), CONTRACT_SEASON
        )
    else:
        logger.info(
            "Nothing to insert — all %d %d contracts already present",
            len(_CONTRACTS),
            CONTRACT_SEASON,
        )


async def seed() -> None:
    """Seed teams, backfill any missing drivers, then link both via contracts."""
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    async with session_factory() as session:
        team_ids = await _ensure_teams(session)
        driver_ids = await _ensure_drivers(session)
        await _ensure_contracts(session, driver_ids, team_ids)
        await session.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
