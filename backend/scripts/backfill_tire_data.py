"""Rebuild TireStint.avg_deg_per_lap from existing LapData records.

avg_deg_per_lap is the slope (seconds/lap) of a linear regression fit over
each stint's valid lap times — a positive slope means lap times are getting
slower as the tyre degrades.

Run via: python backend/scripts/backfill_tire_data.py [--season 2025]
"""

import argparse
import asyncio
import logging

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData, TireStint

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_MIN_LAPS_FOR_REGRESSION = 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recompute TireStint.avg_deg_per_lap from LapData."
    )
    parser.add_argument(
        "--season", type=int, default=None, help="Restrict to a single season (default: all)"
    )
    return parser.parse_args()


def _regression_slope(lap_numbers: list[int], lap_times: list[float]) -> float | None:
    if len(lap_numbers) < _MIN_LAPS_FOR_REGRESSION:
        return None
    slope, _intercept = np.polyfit(lap_numbers, lap_times, 1)
    return float(slope)


async def backfill(season: int | None) -> None:
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    updated = 0
    skipped = 0

    async with session_factory() as db:
        stint_query = select(TireStint)
        if season is not None:
            stint_query = (
                stint_query.join(SessionModel, TireStint.session_id == SessionModel.id)
                .join(Race, SessionModel.race_id == Race.id)
                .where(Race.season == season)
            )

        stints = (await db.execute(stint_query)).scalars().all()
        logger.info("Recomputing avg_deg_per_lap for %d stint(s)", len(stints))

        for stint in stints:
            laps_result = await db.execute(
                select(LapData.lap_number, LapData.lap_time_seconds).where(
                    LapData.session_id == stint.session_id,
                    LapData.driver_id == stint.driver_id,
                    LapData.lap_number >= stint.start_lap,
                    LapData.lap_number <= (stint.end_lap or stint.start_lap),
                    LapData.is_valid.is_(True),
                    LapData.lap_time_seconds.is_not(None),
                )
            )
            rows = laps_result.all()
            if not rows:
                skipped += 1
                continue

            lap_numbers = [row.lap_number for row in rows]
            lap_times = [row.lap_time_seconds for row in rows]
            slope = _regression_slope(lap_numbers, lap_times)

            if slope is None:
                skipped += 1
                continue

            stint.avg_deg_per_lap = slope
            updated += 1

        await db.commit()

    logger.info("Updated %d stint(s), skipped %d (insufficient valid laps)", updated, skipped)
    await engine.dispose()


def main() -> None:
    args = _parse_args()
    asyncio.run(backfill(args.season))


if __name__ == "__main__":
    main()
