"""Live telemetry reads, historical lap aggregation, and session gap computation.

get_live_lap resolves driver_id -> car_number via the reverse mapping
scripts/ingest_live_session.py's run_live_ingestor now writes to Redis
(f1:{season}:{round}:driver:{driver_id}:car_number) alongside the existing
f1:{season}:{round}:car:{car_number}:latest key it has always written. Without
that mapping there is no way to know which car-number-keyed telemetry entry
belongs to a given driver_id.

get_lap_history uses TimescaleDB's time_bucket(), which is a plain scalar
function the extension provides — it does not itself require lap_data to be a
hypertable (see CLAUDE.md's architecture-decisions note on why lap_data isn't
one yet). It should work as-is; the date_trunc fallback below is a safety net
for environments where the extension function still isn't reachable, not a
long-term substitute for the deferred hypertable migration — remove it once
that migration lands and this has run cleanly against it for a while.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.exceptions import TelemetryNotAvailableError
from backend.services.cache_service import cache_get, cacheable

logger = logging.getLogger(__name__)

LAP_HISTORY_BUCKET_INTERVAL = "1 minute"

_LAP_HISTORY_TIME_BUCKET_QUERY = text(
    """
    SELECT time_bucket(:bucket_interval, created_at) AS bucket,
           AVG(sector1_seconds) AS avg_sector1_seconds,
           AVG(sector2_seconds) AS avg_sector2_seconds,
           AVG(sector3_seconds) AS avg_sector3_seconds,
           AVG(lap_time_seconds) AS avg_lap_time_seconds,
           COUNT(*) AS lap_count
    FROM (
        SELECT * FROM lap_data
        WHERE session_id = :session_id AND driver_id = :driver_id
        ORDER BY lap_number DESC
        LIMIT :last_n
    ) recent
    GROUP BY bucket
    ORDER BY bucket DESC
    """
)

_LAP_HISTORY_DATE_TRUNC_QUERY = text(
    """
    SELECT date_trunc('minute', created_at) AS bucket,
           AVG(sector1_seconds) AS avg_sector1_seconds,
           AVG(sector2_seconds) AS avg_sector2_seconds,
           AVG(sector3_seconds) AS avg_sector3_seconds,
           AVG(lap_time_seconds) AS avg_lap_time_seconds,
           COUNT(*) AS lap_count
    FROM (
        SELECT * FROM lap_data
        WHERE session_id = :session_id AND driver_id = :driver_id
        ORDER BY lap_number DESC
        LIMIT :last_n
    ) recent
    GROUP BY bucket
    ORDER BY bucket DESC
    """
)

_GAPS_QUERY = text(
    """
    SELECT driver_id, lap_number, position,
           SUM(lap_time_seconds) OVER (
               PARTITION BY driver_id ORDER BY lap_number
           ) AS cumulative_seconds
    FROM lap_data
    WHERE session_id = :session_id AND lap_time_seconds IS NOT NULL
    ORDER BY driver_id, lap_number
    """
)


def normalize_telemetry(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw FastF1/live-timing telemetry dict into JSON-serialisable primitives.

    Args:
        raw: A raw dict as cached under f1:{season}:{round}:car:{car_number}:latest
            (FastF1-native types: pandas Timedelta/Timestamp, numpy scalars, NaN).
    Returns:
        An equivalent dict containing only JSON-native primitives (str, int,
        float, bool, None, dict, list).
    """
    return {key: _normalize_value(value) for key, value in raw.items()}


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: _normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, pd.Timedelta):
        return value.total_seconds()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        item = value.item()
        if isinstance(item, float) and math.isnan(item):
            return None
        return item
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _car_number_key(season: int, round_number: int, driver_id: uuid.UUID) -> str:
    return f"f1:{season}:{round_number}:driver:{driver_id}:car_number"


def _car_latest_key(season: int, round_number: int, car_number: Any) -> str:
    return f"f1:{season}:{round_number}:car:{car_number}:latest"


async def get_live_lap(
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    season: int,
    round_number: int,
    driver_id: uuid.UUID,
) -> dict[str, Any]:
    """Read the most recent live telemetry sample for one driver.

    This is a pure cache read (the underlying data is pushed by the live
    ingestor, not computed here), so it calls cache_get directly rather than
    going through the cacheable decorator, which assumes a computable fallback.

    Args:
        redis_client: Redis client.
        season, round_number: Race weekend identifiers (the underlying keys are
            season/round-scoped, not session-scoped — see cache_service.py's
            cache_invalidate_session for the same convention).
        driver_id: Driver whose latest sample to read.
    Returns:
        The normalized raw car-telemetry dict.
    Raises:
        TelemetryNotAvailableError: No car-number mapping cached for this driver
            (live ingestion may not be running for this session), or no live
            sample cached (feed stale, or driver not on track).
    """
    car_number = await cache_get(redis_client, _car_number_key(season, round_number, driver_id))
    if car_number is None:
        raise TelemetryNotAvailableError(
            f"No car number mapping cached for driver {driver_id} — is live ingestion running?"
        )

    raw = await cache_get(redis_client, _car_latest_key(season, round_number, car_number))
    if raw is None:
        raise TelemetryNotAvailableError(f"No live telemetry cached for car {car_number}")

    return normalize_telemetry(raw)


async def _fetch_lap_history(
    db: AsyncSession, session_id: uuid.UUID, driver_id: uuid.UUID, last_n: int
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "session_id": str(session_id),
        "driver_id": str(driver_id),
        "last_n": last_n,
    }
    time_bucket_params = {**params, "bucket_interval": LAP_HISTORY_BUCKET_INTERVAL}
    try:
        result = await db.execute(_LAP_HISTORY_TIME_BUCKET_QUERY, time_bucket_params)
    except ProgrammingError:
        await db.rollback()
        logger.warning(
            "time_bucket() unavailable for lap_data — falling back to date_trunc "
            "(see CLAUDE.md's lap_data hypertable note)"
        )
        result = await db.execute(_LAP_HISTORY_DATE_TRUNC_QUERY, params)

    rows = result.mappings().all()
    return [
        {
            "bucket": row["bucket"].isoformat(),
            "avg_sector1_seconds": row["avg_sector1_seconds"],
            "avg_sector2_seconds": row["avg_sector2_seconds"],
            "avg_sector3_seconds": row["avg_sector3_seconds"],
            "avg_lap_time_seconds": row["avg_lap_time_seconds"],
            "lap_count": row["lap_count"],
        }
        for row in rows
    ]


def _key_lap_history(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    last_n: int,
) -> str:
    return f"f1:{season}:{round_number}:telemetry:{driver_id}:history:{last_n}"


@cacheable(ttl=15, key_fn=_key_lap_history)
async def get_lap_history(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    last_n: int,
) -> list[dict[str, Any]]:
    """Time-bucketed sector/lap-time aggregates over a driver's most recent laps.

    Args:
        client: Redis client (cache-aside).
        db: Async DB session.
        season, round_number: Race weekend identifiers, for the cache key.
        session_id: Session to query.
        driver_id: Driver to query.
        last_n: Number of most recent laps to aggregate.
    Returns:
        One dict per LAP_HISTORY_BUCKET_INTERVAL bucket, newest first: bucket,
        avg_sector1/2/3_seconds, avg_lap_time_seconds, lap_count.
    """
    return await _fetch_lap_history(db, session_id, driver_id, last_n)


async def _compute_session_gaps(db: AsyncSession, session_id: uuid.UUID) -> dict[str, Any]:
    result = await db.execute(_GAPS_QUERY, {"session_id": str(session_id)})
    rows = result.mappings().all()

    # Rows are ordered by (driver_id, lap_number) ascending, so the last row seen
    # per driver_id carries that driver's running total — i.e. their full
    # elapsed race time so far.
    latest_per_driver: dict[str, Any] = {}
    for row in rows:
        latest_per_driver[str(row["driver_id"])] = row

    ordered = sorted(latest_per_driver.values(), key=lambda r: r["cumulative_seconds"])
    gaps: list[dict[str, Any]] = []
    for i, row in enumerate(ordered):
        cumulative = row["cumulative_seconds"]
        gap_ahead = cumulative - ordered[i - 1]["cumulative_seconds"] if i > 0 else 0.0
        gap_behind = (
            ordered[i + 1]["cumulative_seconds"] - cumulative if i < len(ordered) - 1 else 0.0
        )
        gaps.append(
            {
                "driver_id": str(row["driver_id"]),
                "lap_number": row["lap_number"],
                "position": i + 1,
                "gap_to_ahead_seconds": float(gap_ahead),
                "gap_to_behind_seconds": float(gap_behind),
            }
        )
    return {"session_id": str(session_id), "gaps": gaps}


def _key_session_gaps(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
) -> str:
    return f"f1:{season}:{round_number}:gaps"


@cacheable(ttl=8, key_fn=_key_session_gaps)
async def get_session_gaps(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
) -> dict[str, Any]:
    """Current lap, track position, and gap to car ahead/behind for every driver.

    Backs the f1:{season}:{round}:gaps cache key already documented in
    CLAUDE.md's Redis Cache Key Schema (TTL 8s). Position is derived from
    cumulative elapsed race time (lower = further ahead), not LapData.position,
    since the latter can lag a lap behind live standings.

    Args:
        client: Redis client (cache-aside).
        db: Async DB session.
        season, round_number: Race weekend identifiers, for the cache key.
        session_id: Session to query.
    Returns:
        Dict with session_id and a gaps list ordered by track position: driver_id,
        lap_number, position, gap_to_ahead_seconds, gap_to_behind_seconds.
    """
    return await _compute_session_gaps(db, session_id)
