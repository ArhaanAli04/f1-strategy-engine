"""Backfill Session.total_laps for existing, genuinely completed R sessions.

total_laps was added by migration 20260918_add_total_laps_to_sessions —
every Session row created before that migration (and every live-ingested
session, until CP2's live LapCount wiring populates it going forward) has
it NULL. For a race that has genuinely finished, MAX(LapData.lap_number)
IS the real race distance — no FastF1 fetch is needed here, unlike
backfill_lap_session_time.py's per-lap backfill.

Deliberately scoped to Race.status == "completed" ONLY (see
docs/live-race-ingestion-and-strategy-gaps-monza-2026.md Issue A's
research-question-2 decision): a partially-live-ingested session (e.g. one
where the ingestor was stopped mid-race, or is still status="scheduled"
because ingest_historical.py never re-processed it — see CLAUDE.md's
Zandvoort R12 note) does NOT have MAX(lap_number) == the real race
distance, and writing a value there would be indistinguishable from a
genuinely-known one to every downstream consumer. Leaving it NULL for
those sessions is the correct, honest choice — they keep falling back to
the same MAX(lap_number)-so-far proxy they use today, no worse off than
before this migration.

R-only, same reasoning as backfill_lap_session_time.py: FP/Q sessions run
on a clock, not a lap count, and Session.total_laps is documented as
always NULL for them.

Idempotent: only sessions with total_laps IS NULL are touched; a session
already populated (by a previous run of this script, or by this fix's own
write paths — ingest_historical.py / ingest_live_session.py's LapCount
handler — going forward) is skipped by the initial query filter, no
per-session work done.

Run via: python backend/scripts/backfill_session_total_laps.py [--season 2025] [--round 9]
"""

import argparse
import asyncio
import logging

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Backfill Session.total_laps for completed R sessions from MAX(lap_number).")
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


async def backfill(season: int | None, round_number: int | None) -> None:
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    updated = 0
    skipped = 0

    async with session_factory() as db:
        query = (
            select(SessionModel.id, Race.season, Race.round_number)
            .join(Race, SessionModel.race_id == Race.id)
            .where(
                SessionModel.session_type == "R",
                SessionModel.total_laps.is_(None),
                Race.status == "completed",
            )
            .order_by(Race.season, Race.round_number)
        )
        if season is not None:
            query = query.where(Race.season == season)
        if round_number is not None:
            query = query.where(Race.round_number == round_number)

        sessions = (await db.execute(query)).all()
        logger.info("Checking %d completed R session(s) with total_laps still NULL", len(sessions))

        for session_id, s_season, s_round in sessions:
            max_lap = (
                await db.execute(
                    select(func.max(LapData.lap_number)).where(LapData.session_id == session_id)
                )
            ).scalar_one_or_none()
            if not max_lap:
                logger.warning("Season %d round %d: no lap_data rows, skipping", s_season, s_round)
                skipped += 1
                continue

            await db.execute(
                update(SessionModel).where(SessionModel.id == session_id).values(total_laps=max_lap)
            )
            await db.commit()
            updated += 1
            logger.info("Season %d round %d: total_laps=%d", s_season, s_round, max_lap)

    await engine.dispose()
    logger.info("Done: %d session(s) updated, %d skipped (no lap data)", updated, skipped)


def main() -> None:
    args = _parse_args()
    asyncio.run(backfill(args.season, args.round))


if __name__ == "__main__":
    main()
