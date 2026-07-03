"""Shared DB upsert helpers used by both ingest_historical.py and ingest_live_session.py."""

import logging
import uuid
from datetime import date

import fastf1
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel

logger = logging.getLogger(__name__)

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


def or_default(value: object, default: str) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return default
    return str(value)


async def get_or_create_circuit(db: AsyncSession, location: str) -> Circuit:
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


async def get_or_create_race(
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


async def get_or_create_session(
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


async def get_or_create_drivers(
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
            full_name=or_default(info.get("FullName"), code),
            nationality=or_default(info.get("CountryCode"), "UNK"),
        )
        db.add(driver)
        await db.flush()
        existing[code] = driver.id
        code_to_id[code] = driver.id

    return code_to_id
