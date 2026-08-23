"""Celery task that persists raw live lap data into Postgres."""

import asyncio
import json
import logging
import uuid

import redis
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_redis_settings
from backend.core.database import get_engine
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
    async with session_factory() as db:
        stmt = (
            pg_insert(LapData)
            .values(id=uuid.uuid4(), **lap.model_dump())
            .on_conflict_do_nothing(index_elements=["session_id", "driver_id", "lap_number"])
        )
        await db.execute(stmt)
        await db.commit()

    # Each task invocation gets its own asyncio.run() (a fresh event loop),
    # but get_engine()'s pooled asyncpg connections are bound to the loop
    # that created them. Dispose here so no connection survives into a
    # later, different-loop task — same convention ingest_historical.py uses.
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
    async with session_factory() as db:
        stmt = (
            pg_insert(TireStint)
            .values(id=uuid.uuid4(), **stint.model_dump())
            .on_conflict_do_nothing(index_elements=["session_id", "driver_id", "stint_number"])
        )
        await db.execute(stmt)
        await db.commit()

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
