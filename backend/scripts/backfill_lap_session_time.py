"""Backfill LapData.session_elapsed_seconds for existing R-session lap rows.

session_elapsed_seconds was added by migration 20260902_add_session_elapsed_
seconds_to_lap_data — every LapData row ingested before that migration has
it NULL. This script recomputes it from FastF1 for already-ingested Race (R)
sessions, the same way ingest_historical.py now populates it on a fresh
ingest (see that module's resolve_session_start/compute_session_elapsed_
seconds, reused here rather than reimplemented).

R-only: those are the only sessions the gaps/simulator endpoints actually
serve (telemetry_service._compute_session_gaps, strategy_service/
prediction_worker's cumulative-time queries) — see CLAUDE.md Deferred
Wiring item A. FP/Q sessions can be backfilled later if a real need for
them arises; nothing currently reads their gaps.

Idempotent: a session with zero NULL session_elapsed_seconds rows left is
skipped without a FastF1 fetch, so a re-run only does work for sessions
that still need it (e.g. a newly-ingested one, or one that failed
previously).

Run via: python backend/scripts/backfill_lap_session_time.py [--season 2025] [--round 9]
"""

import argparse
import asyncio
import logging
from typing import cast

import pandas as pd
import sqlalchemy as sa
from sqlalchemy import bindparam, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData
from backend.scripts._ingest_common import RoundSkippedError
from backend.scripts.ingest_historical import (
    compute_session_elapsed_seconds,
    load_session,
    resolve_session_start,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# update(LapData.__table__), not update(LapData): a Core-level statement
# against the mapped Table, not the ORM-enabled update() — the latter
# detects a bindparam-keyed-by-primary-key params list as an ORM "bulk
# update by primary key" and demands the literal PK attribute name as the
# param key (InvalidRequestError otherwise), which fights the bound-
# parameter naming used here. Correct and simpler for this case: no ORM
# entities are loaded in-session to keep synchronized, this is a plain bulk
# UPDATE ... WHERE id = :_id.
_UPDATE_STMT = (
    update(cast(sa.Table, LapData.__table__))
    .where(LapData.id == bindparam("_id"))
    .values(session_elapsed_seconds=bindparam("elapsed"))
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill LapData.session_elapsed_seconds for R sessions from FastF1."
    )
    parser.add_argument(
        "--season", type=int, default=None, help="Restrict to a single season (default: all)"
    )
    parser.add_argument(
        "--round", type=int, default=None, help="Restrict to a single round (requires --season)"
    )
    args = parser.parse_args()
    if args.round is not None and args.season is None:
        parser.error("--round requires --season")
    return args


async def _backfill_one_session(
    db: AsyncSession, session_id: object, season: int, round_number: int
) -> int:
    """Backfill session_elapsed_seconds for one session's rows.

    Args:
        db: Async DB session.
        session_id: The Session row's id to backfill.
        season, round_number: FastF1 identifiers for this session.
    Returns:
        Number of LapData rows updated (0 if the FastF1 fetch was skipped
        or no DB row matched a FastF1 lap).
    """
    fastf1_session = load_session(season, round_number, "R")
    laps = fastf1_session.laps
    session_start = resolve_session_start(laps)

    elapsed_by_driver_lap: dict[tuple[str, int], float] = {}
    for _, lap in laps.iterrows():
        if pd.isna(lap["LapNumber"]):
            continue
        elapsed = compute_session_elapsed_seconds(lap.get("Time"), session_start)
        if elapsed is None:
            continue
        elapsed_by_driver_lap[(lap["Driver"], int(lap["LapNumber"]))] = elapsed

    # Match against this session's EXISTING LapData rows by (driver code, lap
    # number) — this is a backfill of already-ingested rows, not a fresh
    # insert, so it must never create a Driver/LapData row that isn't already
    # there (unlike _upsert_lap_data's get_or_create_drivers).
    existing_query = (
        select(LapData.id, Driver.code, LapData.lap_number)
        .join(Driver, LapData.driver_id == Driver.id)
        .where(LapData.session_id == session_id, LapData.session_elapsed_seconds.is_(None))
    )
    existing_rows = (await db.execute(existing_query)).all()

    params = [
        {"_id": lap_data_id, "elapsed": elapsed_by_driver_lap[(code, lap_number)]}
        for lap_data_id, code, lap_number in existing_rows
        if (code, lap_number) in elapsed_by_driver_lap
    ]
    unmatched = len(existing_rows) - len(params)
    if unmatched:
        logger.warning(
            "Season %d round %d: %d row(s) had no matching FastF1 lap "
            "(driver code + lap number not found in this fetch)",
            season,
            round_number,
            unmatched,
        )

    if not params:
        return 0

    await db.execute(_UPDATE_STMT, params)
    await db.commit()
    return len(params)


async def backfill(season: int | None, round_number: int | None) -> None:
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    updated_sessions = 0
    updated_rows = 0
    already_done = 0
    skipped = 0

    async with session_factory() as db:
        query = (
            select(SessionModel.id, Race.season, Race.round_number)
            .join(Race, SessionModel.race_id == Race.id)
            .where(SessionModel.session_type == "R")
            .order_by(Race.season, Race.round_number)
        )
        if season is not None:
            query = query.where(Race.season == season)
        if round_number is not None:
            query = query.where(Race.round_number == round_number)

        sessions = (await db.execute(query)).all()
        logger.info("Checking %d R session(s) for missing session_elapsed_seconds", len(sessions))

        for session_id, s_season, s_round in sessions:
            null_count_query = select(func.count()).where(
                LapData.session_id == session_id, LapData.session_elapsed_seconds.is_(None)
            )
            null_count = (await db.execute(null_count_query)).scalar_one()
            if null_count == 0:
                already_done += 1
                continue

            try:
                rows_updated = await _backfill_one_session(db, session_id, s_season, s_round)
            except RoundSkippedError as exc:
                logger.warning("Skipping season %d round %d: %s", s_season, s_round, exc)
                skipped += 1
                continue

            if rows_updated:
                updated_sessions += 1
                updated_rows += rows_updated
                logger.info(
                    "Season %d round %d: updated %d row(s)", s_season, s_round, rows_updated
                )
            else:
                skipped += 1

    await engine.dispose()

    logger.info(
        "Done: %d session(s) updated (%d row(s) total), %d already backfilled, %d skipped",
        updated_sessions,
        updated_rows,
        already_done,
        skipped,
    )


def main() -> None:
    args = _parse_args()
    asyncio.run(backfill(args.season, args.round))


if __name__ == "__main__":
    main()
