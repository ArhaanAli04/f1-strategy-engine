"""Ingest historical FastF1 session data (laps + tire stints) into Postgres.

Run via: make ingest SEASON=2025 ROUND=1 SESSION_TYPE=R
or directly: python backend/scripts/ingest_historical.py --season 2025 --round 1 --session-type R
"""

import argparse
import asyncio
import logging
import os
import subprocess
import sys
import uuid
from datetime import date
from typing import Any, cast

import fastf1
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tqdm import tqdm

from backend.core.config import get_ml_settings
from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData, TireStint

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_BATCH_SIZE = 1000
_VALID_SESSION_TYPES = ("R", "Q", "FP1", "FP2", "FP3")

# Maps FastF1's Event.Location to the exact circuit names seeded in
# scripts/seed_circuits.py (Day 3, 2024-season calendar — unchanged for 2025).
_LOCATION_TO_CIRCUIT_NAME: dict[str, str] = {
    "Sakhir": "Bahrain International Circuit",
    "Jeddah": "Jeddah Corniche Circuit",
    "Melbourne": "Albert Park Circuit",
    "Suzuka": "Suzuka Circuit",
    "Shanghai": "Shanghai International Circuit",
    "Miami": "Miami International Autodrome",
    "Miami Gardens": "Miami International Autodrome",
    "Imola": "Autodromo Enzo e Dino Ferrari",
    "Monaco": "Circuit de Monaco",
    "Barcelona": "Circuit de Barcelona-Catalunya",
    "Montréal": "Circuit Gilles Villeneuve",
    "Montreal": "Circuit Gilles Villeneuve",
    "Spielberg": "Red Bull Ring",
    "Silverstone": "Silverstone Circuit",
    "Budapest": "Hungaroring",
    "Spa-Francorchamps": "Circuit de Spa-Francorchamps",
    "Zandvoort": "Circuit Zandvoort",
    "Monza": "Autodromo Nazionale Monza",
    "Baku": "Baku City Circuit",
    "Singapore": "Marina Bay Street Circuit",
    "Marina Bay": "Marina Bay Street Circuit",
    "Austin": "Circuit of the Americas",
    "Mexico City": "Autodromo Hermanos Rodriguez",
    "São Paulo": "Autodromo Jose Carlos Pace",
    "Sao Paulo": "Autodromo Jose Carlos Pace",
    "Las Vegas": "Las Vegas Strip Circuit",
    "Lusail": "Lusail International Circuit",
    "Yas Island": "Yas Marina Circuit",
    "Abu Dhabi": "Yas Marina Circuit",
}


class RoundSkippedError(Exception):
    """Raised when a round cannot be ingested for a known, non-crash reason."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest a historical FastF1 session.")
    parser.add_argument("--season", type=int, required=True, help="Season year, 2018-2026")
    round_group = parser.add_mutually_exclusive_group(required=True)
    round_group.add_argument("--round", type=int, help="Round number, 1-24")
    round_group.add_argument(
        "--all-rounds",
        action="store_true",
        help="Ingest every round (1-24) for the season sequentially, skipping missing rounds",
    )
    parser.add_argument(
        "--session-type",
        type=str,
        required=True,
        choices=_VALID_SESSION_TYPES,
        help="Session type: R, Q, FP1, FP2, or FP3",
    )
    args = parser.parse_args()

    if not (2018 <= args.season <= 2026):
        parser.error("--season must be between 2018 and 2026")
    if args.round is not None and not (1 <= args.round <= 24):
        parser.error("--round must be between 1 and 24")

    return args


def _load_session(season: int, round_number: int, session_type: str) -> fastf1.core.Session:
    settings = get_ml_settings()
    os.makedirs(settings.fastf1_cache_dir, exist_ok=True)
    fastf1.Cache.enable_cache(settings.fastf1_cache_dir)

    try:
        session = fastf1.get_session(season, round_number, session_type)
        session.load(laps=True, telemetry=False, weather=False, messages=False)
    except Exception as exc:
        raise RoundSkippedError(
            f"Season {season} round {round_number} ({session_type}) could not be loaded: {exc}"
        ) from exc

    return session


async def _get_or_create_circuit(db: AsyncSession, location: str) -> Circuit:
    circuit_name = _LOCATION_TO_CIRCUIT_NAME.get(location)
    if circuit_name is None:
        raise RoundSkippedError(f"No known circuit mapping for FastF1 location '{location}'")

    result = await db.execute(select(Circuit).where(Circuit.name == circuit_name))
    circuit = result.scalar_one_or_none()
    if circuit is None:
        raise RoundSkippedError(
            f"Circuit '{circuit_name}' not found — run `make seed-circuits` first"
        )

    return circuit


async def _get_or_create_race(
    db: AsyncSession, season: int, round_number: int, circuit_id: uuid.UUID, race_date: date
) -> Race:
    result = await db.execute(
        select(Race).where(Race.season == season, Race.round_number == round_number)
    )
    race = result.scalar_one_or_none()
    if race is None:
        race = Race(
            id=uuid.uuid4(),
            season=season,
            round_number=round_number,
            circuit_id=circuit_id,
            race_date=race_date,
            status="completed",
        )
        db.add(race)
        await db.flush()
    return race


async def _get_or_create_session(
    db: AsyncSession, race_id: uuid.UUID, session_type: str, session_date: date
) -> SessionModel:
    result = await db.execute(
        select(SessionModel).where(
            SessionModel.race_id == race_id, SessionModel.session_type == session_type
        )
    )
    session_row = result.scalar_one_or_none()
    if session_row is None:
        session_row = SessionModel(
            id=uuid.uuid4(),
            race_id=race_id,
            session_type=session_type,
            session_date=session_date,
        )
        db.add(session_row)
        await db.flush()
    return session_row


async def _get_or_create_drivers(
    db: AsyncSession, fastf1_session: fastf1.core.Session
) -> dict[str, uuid.UUID]:
    """Map FastF1 driver codes to Driver.id, creating new Driver rows as needed."""
    code_to_id: dict[str, uuid.UUID] = {}

    result = await db.execute(select(Driver.code, Driver.id))
    existing: dict[str, uuid.UUID] = {row.code: row.id for row in result}

    for driver_number in fastf1_session.drivers:
        try:
            info = fastf1_session.get_driver(driver_number)
        except Exception as exc:  # noqa: BLE001 — per-driver skip, logged below
            logger.warning("Skipping unresolvable driver number %s: %s", driver_number, exc)
            continue

        code = info.get("Abbreviation")
        if not code:
            logger.warning("Skipping driver number %s with no Abbreviation", driver_number)
            continue

        if code in existing:
            code_to_id[code] = existing[code]
            continue

        driver = Driver(
            id=uuid.uuid4(),
            code=code,
            full_name=_or_default(info.get("FullName"), code),
            nationality=_or_default(info.get("CountryCode"), "UNK"),
        )
        db.add(driver)
        await db.flush()
        existing[code] = driver.id
        code_to_id[code] = driver.id

    return code_to_id


def _lap_time_to_seconds(value: pd.Timedelta) -> float | None:
    if pd.isna(value):
        return None
    return float(value.total_seconds())


def _or_default(value: object, default: str) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return default
    return str(value)


async def _upsert_lap_data(
    db: AsyncSession,
    session_id: uuid.UUID,
    session_type: str,
    laps: pd.DataFrame,
    driver_code_to_id: dict[str, uuid.UUID],
) -> int:
    rows: list[dict[str, object]] = []

    for _, lap in tqdm(laps.iterrows(), total=len(laps), desc=f"laps ({session_type})"):
        try:
            driver_id = driver_code_to_id.get(lap["Driver"])
            if driver_id is None:
                logger.warning("Skipping lap for unmapped driver code '%s'", lap["Driver"])
                continue
            if pd.isna(lap["LapNumber"]):
                logger.warning("Skipping lap with missing LapNumber for driver '%s'", lap["Driver"])
                continue

            rows.append(
                {
                    "id": uuid.uuid4(),
                    "session_id": session_id,
                    "driver_id": driver_id,
                    "lap_number": int(lap["LapNumber"]),
                    "lap_time_seconds": _lap_time_to_seconds(lap["LapTime"]),
                    "compound": _or_default(lap["Compound"], "UNKNOWN"),
                    "tyre_age_laps": (int(lap["TyreLife"]) if not pd.isna(lap["TyreLife"]) else 0),
                    "is_valid": (
                        bool(lap["IsAccurate"]) if not pd.isna(lap["IsAccurate"]) else False
                    ),
                    "sector1_seconds": _lap_time_to_seconds(lap["Sector1Time"]),
                    "sector2_seconds": _lap_time_to_seconds(lap["Sector2Time"]),
                    "sector3_seconds": _lap_time_to_seconds(lap["Sector3Time"]),
                }
            )
        except Exception as exc:  # noqa: BLE001 — corrupt lap row, skip and continue
            logger.warning("Skipping corrupt lap row: %s", exc)

    inserted = 0
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i : i + _BATCH_SIZE]
        stmt = (
            pg_insert(LapData)
            .values(batch)
            .on_conflict_do_nothing(index_elements=["session_id", "driver_id", "lap_number"])
        )
        result = cast(CursorResult[Any], await db.execute(stmt))
        inserted += result.rowcount or 0

    await db.commit()
    return inserted


async def _upsert_tire_stints(
    db: AsyncSession,
    session_id: uuid.UUID,
    laps: pd.DataFrame,
    driver_code_to_id: dict[str, uuid.UUID],
) -> int:
    rows: list[dict[str, object]] = []

    grouped = laps.dropna(subset=["Stint"]).groupby(["Driver", "Stint"])
    for (driver_code, stint_number), stint_laps in grouped:
        driver_id = driver_code_to_id.get(driver_code)
        if driver_id is None:
            continue

        compounds = stint_laps["Compound"].dropna()
        if compounds.empty:
            continue

        rows.append(
            {
                "id": uuid.uuid4(),
                "session_id": session_id,
                "driver_id": driver_id,
                "stint_number": int(stint_number),
                "compound": compounds.iloc[0],
                "start_lap": int(stint_laps["LapNumber"].min()),
                "end_lap": int(stint_laps["LapNumber"].max()),
                "avg_deg_per_lap": None,
            }
        )

    inserted = 0
    for i in range(0, len(rows), _BATCH_SIZE):
        batch = rows[i : i + _BATCH_SIZE]
        stmt = (
            pg_insert(TireStint)
            .values(batch)
            .on_conflict_do_nothing(index_elements=["session_id", "driver_id", "stint_number"])
        )
        result = cast(CursorResult[Any], await db.execute(stmt))
        inserted += result.rowcount or 0

    await db.commit()
    return inserted


async def ingest(season: int, round_number: int, session_type: str) -> None:
    fastf1_session = _load_session(season, round_number, session_type)

    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    async with session_factory() as db:
        circuit = await _get_or_create_circuit(db, fastf1_session.event["Location"])
        race = await _get_or_create_race(
            db,
            season=season,
            round_number=round_number,
            circuit_id=circuit.id,
            race_date=fastf1_session.event["EventDate"].date(),
        )
        session_row = await _get_or_create_session(
            db,
            race_id=race.id,
            session_type=session_type,
            session_date=fastf1_session.event["EventDate"].date(),
        )
        await db.commit()

        driver_code_to_id = await _get_or_create_drivers(db, fastf1_session)
        await db.commit()

        laps = fastf1_session.laps
        lap_count = await _upsert_lap_data(
            db, session_row.id, session_type, laps, driver_code_to_id
        )
        stint_count = await _upsert_tire_stints(db, session_row.id, laps, driver_code_to_id)

        logger.info(
            "Season %d round %d (%s): inserted %d lap(s), %d stint(s)",
            season,
            round_number,
            session_type,
            lap_count,
            stint_count,
        )

    await engine.dispose()


def _ingest_all_rounds(season: int, session_type: str) -> None:
    """Ingest every round of a season sequentially, one subprocess per round.

    Runs each round in its own process (rather than looping in-process) so a
    fresh DB engine is created per round and one round's failure can never
    leave shared state (engine, event loop) corrupted for the next. This also
    lets the Makefile stay shell-agnostic — the loop lives in Python, not in
    bash-specific recipe syntax.
    """
    for round_number in range(1, 25):
        result = subprocess.run(  # noqa: S603 — args are ints and argparse-validated choices
            [
                sys.executable,
                __file__,
                "--season",
                str(season),
                "--round",
                str(round_number),
                "--session-type",
                session_type,
            ],
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "Round %d exited with code %d, continuing", round_number, result.returncode
            )


def main() -> None:
    args = _parse_args()

    if args.all_rounds:
        _ingest_all_rounds(args.season, args.session_type)
        return

    try:
        asyncio.run(ingest(args.season, args.round, args.session_type))
    except RoundSkippedError as exc:
        logger.warning("Skipping: %s", exc)


if __name__ == "__main__":
    main()
