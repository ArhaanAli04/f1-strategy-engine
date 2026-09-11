"""Rebuild TireStint.avg_deg_per_lap from existing LapData records.

avg_deg_per_lap is the slope (seconds/lap) of a linear regression fit over
each stint's valid, FUEL-CORRECTED lap times — a positive slope means lap
times are getting slower as the tyre degrades.

The fuel correction (added 2026-09-09) is not optional bookkeeping: a car
sheds ~110kg across a race and gets steadily faster for that reason alone, so
regressing raw lap times against lap number measures mostly fuel burn and
reports a NEGATIVE slope for a normally-degrading tyre. Measured before this
fix, the stored values had a negative median for every compound across all
7381 stints — i.e. this column claimed F1 tyres get faster the longer you run
them. tire_deg_model.fuel_load_penalty_seconds is the same fuel model the
tyre-degradation models themselves train against, reused here deliberately so
the two definitions of "degradation" can't drift apart. See
docs/tire-deg-model-quality-and-rival-pit-behavior.md.

Re-run after this fix to correct already-backfilled seasons — the values
stored by earlier runs carry the uncorrected slope.

Run via: python backend/scripts/backfill_tire_data.py [--season 2025]
"""

import argparse
import asyncio
import logging
from uuid import UUID

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData, TireStint
from backend.services.ml import tire_deg_model

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


def _regression_slope(
    lap_numbers: list[int], lap_times: list[float], laps_in_session: int
) -> float | None:
    """Degradation slope (s/lap) over one stint's fuel-corrected lap times.

    Args:
        lap_numbers: Lap numbers of this stint's valid timed laps.
        lap_times: Raw lap times (seconds) for the same laps, same order.
        laps_in_session: Total laps in the session, for the fuel correction's
            race-fraction term.
    Returns:
        The fitted slope in seconds per lap — positive means the tyre is
        degrading — or None if the stint has fewer than
        _MIN_LAPS_FOR_REGRESSION timed laps to fit against.
    """
    if len(lap_numbers) < _MIN_LAPS_FOR_REGRESSION:
        return None
    laps = np.asarray(lap_numbers, dtype=float)
    corrected = np.asarray(lap_times, dtype=float) - tire_deg_model.fuel_load_penalty_seconds(
        laps, float(laps_in_session)
    )
    slope, _intercept = np.polyfit(laps, corrected, 1)
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

        # Race distance per session, for the fuel correction. Same
        # MAX(lap_number) proxy the rest of this codebase uses for "total
        # laps" (train_models' laps_in_session, _resolve_inference_context) —
        # no total_laps column exists on Session/Race. Fetched once for every
        # session up front rather than per stint: ~150 sessions vs ~7000
        # stints.
        session_lap_rows = (
            await db.execute(
                select(LapData.session_id, func.max(LapData.lap_number)).group_by(
                    LapData.session_id
                )
            )
        ).all()
        laps_in_session: dict[UUID, int] = dict(session_lap_rows)  # type: ignore[arg-type]

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
            slope = _regression_slope(
                lap_numbers, lap_times, laps_in_session.get(stint.session_id) or max(lap_numbers)
            )

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
