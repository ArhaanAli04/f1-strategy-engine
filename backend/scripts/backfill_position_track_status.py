"""Backfill lap_data.position and lap_data.track_status from FastF1.

FastF1's Laps dataframe carries Position and TrackStatus per lap alongside
LapTime/Compound/etc — all fetched by the same laps=True call ingest_historical.py
already makes — but neither column was persisted before migration
c049f6f51210. This re-loads every already-ingested Race session and updates
the existing lap_data rows in place; it inserts nothing new.

Run via: python backend/scripts/backfill_position_track_status.py [--season 2025]
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

from backend.core.config import get_ml_settings
from backend.core.database import get_engine
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData
from backend.scripts._ingest_common import RoundSkippedError, get_or_create_drivers

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_BATCH_SIZE = 1000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill lap_data.position and lap_data.track_status from FastF1."
    )
    parser.add_argument(
        "--season", type=int, default=None, help="Restrict to a single season (default: all)"
    )
    return parser.parse_args()


async def _get_race_sessions(
    db: AsyncSession, season: int | None
) -> list[tuple[int, int, uuid.UUID]]:
    """Args: db session, optional season filter.

    Returns: (season, round_number, session_id) tuples.
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


def _load_laps(season: int, round_number: int) -> tuple[fastf1.core.Session, pd.DataFrame]:
    settings = get_ml_settings()
    os.makedirs(settings.fastf1_cache_dir, exist_ok=True)
    fastf1.Cache.enable_cache(settings.fastf1_cache_dir)

    # fastf1_session.laps is accessed inside the try block too: FastF1 sometimes
    # swallows an internal failure during load() (logging it rather than raising)
    # and leaves laps unpopulated, which only surfaces as DataNotLoadedError on
    # the later .laps property access — e.g. Italian GP 2018's Ergast fallback
    # crashing on a Session internal attribute that no longer exists.
    try:
        fastf1_session = fastf1.get_session(season, round_number, "R")
        fastf1_session.load(laps=True, telemetry=False, weather=False, messages=False)
        laps = fastf1_session.laps
    except Exception as exc:
        raise RoundSkippedError(
            f"Season {season} round {round_number} (R) could not be loaded: {exc}"
        ) from exc

    return fastf1_session, laps


async def _backfill_session(
    db: AsyncSession,
    session_id: uuid.UUID,
    season: int,
    round_number: int,
) -> int:
    fastf1_session, laps = _load_laps(season, round_number)
    driver_code_to_id = await get_or_create_drivers(db, fastf1_session)
    await db.commit()

    updates: list[dict[str, object]] = []
    for _, lap in laps.iterrows():
        driver_id = driver_code_to_id.get(lap["Driver"])
        if driver_id is None or pd.isna(lap["LapNumber"]):
            continue

        position = int(lap["Position"]) if not pd.isna(lap["Position"]) else None
        track_status = str(lap["TrackStatus"]) if not pd.isna(lap["TrackStatus"]) else None

        updates.append(
            {
                "b_session_id": session_id,
                "b_driver_id": driver_id,
                "b_lap_number": int(lap["LapNumber"]),
                "b_position": position,
                "b_track_status": track_status,
            }
        )

    if not updates:
        return 0

    # Uses the Core Table (LapData.__table__), not update(LapData) — the ORM-enabled
    # form treats executemany-style params as a bulk update-by-primary-key and
    # requires lap_data.id in every row, which this backfill never loads. __table__
    # is typed as the broader FromClause by SQLAlchemy's stubs, hence the cast.
    lap_data_table = cast(Table, LapData.__table__)
    stmt = (
        update(lap_data_table)
        .where(
            lap_data_table.c.session_id == bindparam("b_session_id"),
            lap_data_table.c.driver_id == bindparam("b_driver_id"),
            lap_data_table.c.lap_number == bindparam("b_lap_number"),
        )
        .values(position=bindparam("b_position"), track_status=bindparam("b_track_status"))
    )

    # asyncpg's executemany doesn't report a real rowcount (SQLAlchemy surfaces -1
    # per batch here), so "updated" counts rows submitted, not a driver-confirmed
    # count. Verified directly against the DB that the executemany UPDATE does
    # apply correctly despite the -1.
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

    logger.info("Backfilling %d race session(s)", len(sessions))

    total_updated = 0
    for season_year, round_number, session_id in sessions:
        async with session_factory() as db:
            try:
                updated = await _backfill_session(db, session_id, season_year, round_number)
            except RoundSkippedError as exc:
                logger.warning("Skipping: %s", exc)
                continue

        total_updated += updated
        logger.info("Season %d round %d: updated %d lap row(s)", season_year, round_number, updated)

    logger.info("Done. Total lap rows updated: %d", total_updated)
    await engine.dispose()


def main() -> None:
    args = _parse_args()
    asyncio.run(backfill(args.season))


if __name__ == "__main__":
    main()
