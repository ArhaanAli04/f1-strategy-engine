"""Celery task that persists raw live lap data into Postgres."""

import asyncio
import json
import logging
import uuid

import redis
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_redis_settings
from backend.core.database import get_engine
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData, TireStint
from backend.schemas.telemetry_schema import LapDataCreate, TireStintCreate
from backend.workers.celery_app import app

logger = logging.getLogger(__name__)

_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def _publish_lap_completed(lap: LapDataCreate) -> None:
    """Publish a lap-summary event for the /ws/telemetry/{session_id} endpoint.

    New pub/sub channel (no TTL, matching f1:alerts:{session_id}'s convention
    in CLAUDE.md's Redis Cache Key Schema): f1:telemetry:{session_id}:laps.
    Payload is the lap-summary subset of LapCompletedEvent — the WS route
    fills in speed_kmh/throttle_pct/brake/gear/drs itself at delivery time
    from the live CarData cache (see telemetry_service.get_live_car_channels),
    since that's a live-at-broadcast-time value, not something to snapshot here.

    Args:
        lap: The lap just persisted by _persist_lap.
    Returns:
        None.
    """
    client = redis.Redis.from_url(get_redis_settings().redis_url, decode_responses=True)
    try:
        payload = {
            "driver_id": str(lap.driver_id),
            "session_id": str(lap.session_id),
            "lap_number": lap.lap_number,
            "lap_time_seconds": lap.lap_time_seconds,
            "compound": lap.compound,
            "sector1_seconds": lap.sector1_seconds,
            "sector2_seconds": lap.sector2_seconds,
            "sector3_seconds": lap.sector3_seconds,
        }
        client.publish(f"f1:telemetry:{lap.session_id}:laps", json.dumps(payload))
    finally:
        client.close()


async def _persist_lap(lap: LapDataCreate) -> None:
    """Upsert a single live lap into lap_data, ignoring duplicates.

    Args:
        lap: Validated lap payload from the live ingestor.
    Returns:
        None.
    """
    session_factory = _get_session_factory()
    try:
        async with session_factory() as db:
            stmt = (
                pg_insert(LapData)
                .values(id=uuid.uuid4(), **lap.model_dump())
                .on_conflict_do_nothing(index_elements=["session_id", "driver_id", "lap_number"])
            )
            await db.execute(stmt)
            await db.commit()
    finally:
        # Each task invocation gets its own asyncio.run() (a fresh event
        # loop), but get_engine()'s pooled asyncpg connections are bound to
        # the loop that created them. Dispose here so no connection survives
        # into a later, different-loop task — same convention
        # ingest_historical.py uses. In a finally (not just after the `async
        # with` block, as this was before this fix): a bare
        # `await get_engine().dispose()` placed after the block is silently
        # SKIPPED whenever the block raises (a DB constraint violation,
        # anything LapDataCreate.model_validate didn't already catch) —
        # identical shape to, and fixed the same way as,
        # prediction_worker._run_simulation's dispose bug (CLAUDE.md's
        # Notes, item 1d), which is how this sibling gap was originally
        # found. Left unguarded, a failed persist leaks a pooled asyncpg
        # connection into whatever asyncio.run() call happens next in this
        # worker process (slow pool exhaustion over many failures, not an
        # immediate crash).
        await get_engine().dispose()

    _publish_lap_completed(lap)


@app.task(name="process_lap")  # type: ignore[untyped-decorator]
def process_lap(raw_lap: dict[str, object]) -> None:
    """Validate and persist a raw lap dict dispatched by the live ingestor.

    Args:
        raw_lap: Raw lap fields matching LapDataCreate's schema.
    Returns:
        None.
    """
    lap = LapDataCreate.model_validate(raw_lap)
    asyncio.run(_persist_lap(lap))


async def _persist_tire_stint(stint: TireStintCreate) -> None:
    """Insert a new tire stint row, ignoring duplicates.

    Args:
        stint: Validated stint payload from the live ingestor's TimingAppData handler.
    Returns:
        None.
    """
    session_factory = _get_session_factory()
    try:
        async with session_factory() as db:
            stmt = (
                pg_insert(TireStint)
                .values(id=uuid.uuid4(), **stint.model_dump())
                .on_conflict_do_nothing(index_elements=["session_id", "driver_id", "stint_number"])
            )
            await db.execute(stmt)
            await db.commit()
    finally:
        # Same dispose-on-exception fix as _persist_lap above — see that
        # function's own comment for the full explanation. Identical shape,
        # found in the same file while fixing that one (not part of item
        # 11's original scope, fixed alongside on request since it's the
        # same proven fix pattern).
        await get_engine().dispose()


@app.task(name="record_tire_stint")  # type: ignore[untyped-decorator]
def record_tire_stint(raw_stint: dict[str, object]) -> None:
    """Validate and persist a new tire stint dispatched by the live ingestor.

    Args:
        raw_stint: Raw stint fields matching TireStintCreate's schema.
    Returns:
        None.
    """
    stint = TireStintCreate.model_validate(raw_stint)
    asyncio.run(_persist_tire_stint(stint))


async def _persist_session_total_laps(session_id: uuid.UUID, total_laps: int) -> None:
    """Set sessions.total_laps for one session, once resolved from the live feed.

    Plain UPDATE, not an upsert — unlike process_lap/record_tire_stint's
    per-lap rows, the Session row is always already created (by
    ingest_live_session.py's own _resolve_context, before the ingestor's
    SignalR connection ever opens), so there is nothing to insert here; a
    missing row would be a real bug (the session_id the live ingestor is
    running under doesn't exist), not a legitimate race to handle quietly.

    Args:
        session_id: Session whose total_laps to set.
        total_laps: Real scheduled race distance, from the live feed's
            LapCount.TotalLaps (see ingest_live_session.py's
            _handle_lap_count and docs/live-race-ingestion-and-strategy-
            gaps-monza-2026.md Issue A).
    Returns:
        None.
    """
    session_factory = _get_session_factory()
    try:
        async with session_factory() as db:
            await db.execute(
                update(SessionModel)
                .where(SessionModel.id == session_id)
                .values(total_laps=total_laps)
            )
            await db.commit()
    finally:
        # Same dispose-on-exception convention as _persist_lap/
        # _persist_tire_stint above — see _persist_lap's own comment.
        await get_engine().dispose()


@app.task(name="update_session_total_laps")  # type: ignore[untyped-decorator]
def update_session_total_laps(session_id: str, total_laps: int) -> None:
    """Persist the live feed's LapCount.TotalLaps for one session.

    Args:
        session_id: Session UUID (string form, as dispatched by the live ingestor).
        total_laps: Real scheduled race distance.
    Returns:
        None.
    """
    asyncio.run(_persist_session_total_laps(uuid.UUID(session_id), total_laps))
