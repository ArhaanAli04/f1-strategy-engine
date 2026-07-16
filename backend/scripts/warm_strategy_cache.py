"""Pre-compute and cache strategy predictions for every driver in a session.

Intended trigger: the live ingestor (scripts/ingest_live_session.py) calls this
at race-day session startup, once lap data starts flowing, so the first user
request against /strategy/{session_id}/{driver_id}/pit-window or
/strategy/{session_id}/overview is never a cold cache miss (both are already
@cacheable(ttl=30) in strategy_service.py — this script just pays that first
compute cost proactively instead of on a real user's request).

Driver roster: CLAUDE.md's Deferred Wiring Gaps section notes driver_contracts
is currently empty (no seed_teams.py yet), so it cannot be used to enumerate
"the 20 drivers in this session." Instead the roster is read from lap_data's
distinct driver_id for the session — which also means this can only warm
predictions once at least one lap has been recorded for each driver, not at
the literal instant a session opens with zero laps. That's consistent with
get_optimal_pit_window's own requirement (via _current_state) of at least one
lap_data row per driver.

Graceful no-op (log a warning, exit 0, never raise) in two cases, per spec:
- session_id doesn't resolve to a real session (resolve_season_round's
  NotFoundError) — "no active session".
- session resolves but has no lap_data yet — nothing to warm yet.
Per-driver failures (NotFoundError for a driver with no laps yet — e.g. DNS,
or hasn't started; ModelNotLoadedError) are logged and skipped individually,
they must not abort warming for the rest of the field.

Run via: python backend/scripts/warm_strategy_cache.py --session-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_redis_settings
from backend.core.database import get_engine
from backend.core.exceptions import ModelNotLoadedError, NotFoundError
from backend.models.telemetry import LapData
from backend.services import strategy_service

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pre-compute and cache strategy predictions for every driver in a session."
    )
    parser.add_argument(
        "--session-id", type=uuid.UUID, required=True, help="Session to warm the cache for"
    )
    return parser.parse_args()


async def _session_driver_ids(db: AsyncSession, session_id: uuid.UUID) -> list[uuid.UUID]:
    """Distinct driver_id values with at least one lap_data row in this session.

    Args:
        db: Async DB session.
        session_id: Session to inspect.
    Returns:
        Driver IDs — the roster proxy this script warms (see module docstring
        for why driver_contracts isn't used instead).
    """
    query = select(LapData.driver_id).where(LapData.session_id == session_id).distinct()
    return [row[0] for row in (await db.execute(query)).all()]


async def warm_session(session_id: uuid.UUID) -> None:
    """Warm the competitor-overview and per-driver pit-window caches for one session.

    Args:
        session_id: Session to warm.
    Returns:
        None. Logs progress; never raises — see module docstring for the
        graceful-no-op contract this script must honour when triggered by the
        live ingestor at session startup.
    """
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    redis_client: aioredis.Redis = aioredis.Redis.from_url(  # type: ignore[type-arg]
        get_redis_settings().redis_url, decode_responses=True
    )

    try:
        async with session_factory() as db:
            try:
                season, round_number = await strategy_service.resolve_season_round(db, session_id)
            except NotFoundError:
                logger.warning(
                    "Session %s not found — no active session, nothing to warm", session_id
                )
                return

            driver_ids = await _session_driver_ids(db, session_id)
            if not driver_ids:
                logger.warning(
                    "No lap data yet for session %s (season %d round %d) — nothing to warm",
                    session_id,
                    season,
                    round_number,
                )
                return

            logger.info(
                "Warming strategy cache: session %s, season %d round %d, %d driver(s)",
                session_id,
                season,
                round_number,
                len(driver_ids),
            )

            try:
                await strategy_service.get_competitor_predicted_strategy(
                    redis_client, db, season, round_number, session_id
                )
                logger.info("Warmed competitor-overview cache")
            except (NotFoundError, ModelNotLoadedError) as exc:
                logger.warning("Could not warm competitor-overview cache: %s", exc)

            warmed = 0
            for driver_id in driver_ids:
                try:
                    await strategy_service.get_pit_window_with_explanation(
                        redis_client, db, season, round_number, session_id, driver_id
                    )
                    warmed += 1
                except (NotFoundError, ModelNotLoadedError) as exc:
                    logger.warning("Could not warm pit window for driver %s: %s", driver_id, exc)

            logger.info("Warmed pit-window cache for %d/%d driver(s)", warmed, len(driver_ids))
    finally:
        await redis_client.aclose()  # type: ignore[attr-defined]
        await engine.dispose()


def main() -> None:
    args = _parse_args()
    asyncio.run(warm_session(args.session_id))


if __name__ == "__main__":
    main()
