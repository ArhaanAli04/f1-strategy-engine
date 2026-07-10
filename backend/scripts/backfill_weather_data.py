"""Backfill lap_data.track_temp and lap_data.air_temp from FastF1 weather data.

track_temp/air_temp were dropped from tire_deg_model's original Day 7 spec
because ingest_historical.py loads sessions with weather=False (see CLAUDE.md
Deferred Schema Changes). This re-loads every already-ingested Race session
with weather=True, joins each lap to its nearest weather sample by session
Time via merge_asof, and updates the existing lap_data rows in place; it
inserts nothing new.

Some older seasons/sessions have no weather_data at all — those are skipped
with a warning rather than failing the whole run, since partial coverage is
expected (see CLAUDE.md Data Quality Notes).

Run via: python backend/scripts/backfill_weather_data.py [--season 2025]
"""

import argparse
import asyncio
import logging
import os
import uuid
from typing import cast

import fastf1
import pandas as pd
from sqlalchemy import Table, bindparam, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tqdm import tqdm

from backend.core.config import get_ml_settings
from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData
from backend.scripts._ingest_common import RoundSkippedError

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_BATCH_SIZE = 1000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill lap_data.track_temp and lap_data.air_temp from FastF1 weather data."
    )
    parser.add_argument(
        "--season", type=int, default=None, help="Restrict to a single season (default: all)"
    )
    return parser.parse_args()


async def _get_race_sessions(
    db: AsyncSession, season: int | None
) -> list[tuple[int, int, uuid.UUID]]:
    """Args: db session, optional season filter.

    Returns: (season, round_number, session_id) tuples for every Race session.
    """
    query = (
        select(Race.season, Race.round_number, SessionModel.id)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .where(SessionModel.session_type == "R")
        .order_by(Race.season, Race.round_number)
    )
    if season is not None:
        query = query.where(Race.season == season)

    result = await db.execute(query)
    return [(row.season, row.round_number, row.id) for row in result]


async def _driver_code_to_id(db: AsyncSession) -> dict[str, uuid.UUID]:
    """Existing Driver.code -> Driver.id mapping, shared across all sessions.

    Every driver referenced by already-ingested lap_data rows must already
    have a Driver row (created during historical ingestion), so this backfill
    only looks drivers up — it never creates them.
    """
    result = await db.execute(select(Driver.code, Driver.id))
    return {row.code: row.id for row in result}


def _load_laps_and_weather(season: int, round_number: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    settings = get_ml_settings()
    os.makedirs(settings.fastf1_cache_dir, exist_ok=True)
    fastf1.Cache.enable_cache(settings.fastf1_cache_dir)

    try:
        fastf1_session = fastf1.get_session(season, round_number, "R")
        fastf1_session.load(laps=True, telemetry=False, weather=True, messages=False)
        laps = fastf1_session.laps
        weather = fastf1_session.weather_data
    except Exception as exc:
        raise RoundSkippedError(
            f"Season {season} round {round_number} (R) could not be loaded: {exc}"
        ) from exc

    if weather.empty:
        raise RoundSkippedError(f"Season {season} round {round_number} (R) has no weather data")

    return laps, weather


def _nearest_weather_join(laps: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    """Join each lap to its closest weather sample by session Time.

    Args:
        laps: FastF1 Laps dataframe; must include Time, Driver, LapNumber.
        weather: FastF1 weather_data dataframe; must include Time, AirTemp, TrackTemp.
    Returns:
        laps (rows with NaT Time dropped) with AirTemp/TrackTemp columns merged in.
    """
    laps_sorted = laps.dropna(subset=["Time"]).sort_values("Time")
    weather_sorted = weather.dropna(subset=["Time"]).sort_values("Time")
    merged = pd.merge_asof(
        laps_sorted,
        weather_sorted[["Time", "AirTemp", "TrackTemp"]],
        on="Time",
        direction="nearest",
    )
    return merged


async def _backfill_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    season: int,
    round_number: int,
    driver_code_to_id: dict[str, uuid.UUID],
) -> int:
    laps, weather = _load_laps_and_weather(season, round_number)
    merged = _nearest_weather_join(laps, weather)

    updates: list[dict[str, object]] = []
    for _, lap in merged.iterrows():
        driver_id = driver_code_to_id.get(lap["Driver"])
        if driver_id is None or pd.isna(lap["LapNumber"]):
            continue

        track_temp = float(lap["TrackTemp"]) if not pd.isna(lap["TrackTemp"]) else None
        air_temp = float(lap["AirTemp"]) if not pd.isna(lap["AirTemp"]) else None
        if track_temp is None and air_temp is None:
            continue

        updates.append(
            {
                "b_session_id": session_id,
                "b_driver_id": driver_id,
                "b_lap_number": int(lap["LapNumber"]),
                "b_track_temp": track_temp,
                "b_air_temp": air_temp,
            }
        )

    if not updates:
        return 0

    # See backfill_position_track_status.py for why __table__ + cast(Table, ...)
    # is used instead of the ORM-enabled update(LapData) form.
    lap_data_table = cast(Table, LapData.__table__)
    stmt = (
        update(lap_data_table)
        .where(
            lap_data_table.c.session_id == bindparam("b_session_id"),
            lap_data_table.c.driver_id == bindparam("b_driver_id"),
            lap_data_table.c.lap_number == bindparam("b_lap_number"),
        )
        .values(track_temp=bindparam("b_track_temp"), air_temp=bindparam("b_air_temp"))
    )

    for i in range(0, len(updates), _BATCH_SIZE):
        batch = updates[i : i + _BATCH_SIZE]
        await db.execute(stmt, batch)

    await db.commit()
    return len(updates)


async def backfill(season: int | None) -> None:
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    async with session_factory() as db:
        sessions = await _get_race_sessions(db, season)
        driver_code_to_id = await _driver_code_to_id(db)

    logger.info("Backfilling weather data for %d race session(s)", len(sessions))

    total_updated = 0
    for season_year, round_number, session_id in tqdm(sessions, desc="Sessions", unit="session"):
        async with session_factory() as db:
            try:
                updated = await _backfill_session(
                    db, session_id, season_year, round_number, driver_code_to_id
                )
            except RoundSkippedError as exc:
                logger.warning("Skipping: %s", exc)
                continue

        total_updated += updated
        logger.info(
            "Season %d round %d: updated %d lap row(s) with weather data",
            season_year,
            round_number,
            updated,
        )

    logger.info("Done. Total lap rows updated: %d", total_updated)
    await engine.dispose()


def main() -> None:
    args = _parse_args()
    asyncio.run(backfill(args.season))


if __name__ == "__main__":
    main()
