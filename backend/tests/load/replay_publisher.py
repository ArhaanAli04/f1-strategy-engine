"""Replays a historical session's real lap_data rows onto the live WS pub/sub channel.

Companion to locustfile.py's WebSocketUser: nothing publishes to
f1:telemetry:{session_id}:laps for a historical (already-completed) session —
that channel is normally fed by workers/telemetry_worker.py's
_publish_lap_completed, which only runs as a side effect of live ingestion.
Without this script running alongside a Locust load test, WebSocketUser would
sit connected receiving zero messages, and the WS latency numbers in the
report would be empty rather than meaningful.

Publishes the exact same payload shape telemetry_worker._publish_lap_completed
uses (driver_id, session_id, lap_number, lap_time_seconds, compound,
sector1/2/3_seconds) — real ingested data, not synthetic — cycling through the
session's laps indefinitely so lap-completion events keep landing for the
duration of a load test run.

Run alongside a Locust run (separate terminal, Ctrl+C to stop):
    python backend/tests/load/replay_publisher.py --session-id <uuid> --rate 2
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import time
import uuid

import redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_redis_settings
from backend.core.database import get_engine
from backend.models.telemetry import LapData

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_RATE_PER_SECOND = 1.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a historical session's real laps onto the live WS pub/sub channel."
    )
    parser.add_argument("--session-id", type=uuid.UUID, required=True, help="Session to replay")
    parser.add_argument(
        "--rate",
        type=float,
        default=DEFAULT_RATE_PER_SECOND,
        help="Lap-completion events published per second (default: %(default)s)",
    )
    return parser.parse_args()


async def _fetch_laps(session_id: uuid.UUID) -> list[dict[str, object]]:
    """Real lap_data rows for a session, shaped like _publish_lap_completed's output.

    Args:
        session_id: Session to replay.
    Returns:
        One dict per lap, ordered by lap_number then driver_id (a stable,
        arbitrary-but-reproducible replay order — original wall-clock arrival
        order isn't persisted, so this is the closest deterministic proxy).
    """
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    query = (
        select(LapData)
        .where(LapData.session_id == session_id, LapData.lap_time_seconds.is_not(None))
        .order_by(LapData.lap_number, LapData.driver_id)
    )
    async with session_factory() as db:
        rows = (await db.execute(query)).scalars().all()
    await engine.dispose()

    return [
        {
            "driver_id": str(row.driver_id),
            "session_id": str(row.session_id),
            "lap_number": row.lap_number,
            "lap_time_seconds": row.lap_time_seconds,
            "compound": row.compound,
            "sector1_seconds": row.sector1_seconds,
            "sector2_seconds": row.sector2_seconds,
            "sector3_seconds": row.sector3_seconds,
        }
        for row in rows
    ]


def replay(session_id: uuid.UUID, rate_per_second: float) -> None:
    """Publish real lap-completion events onto f1:telemetry:{session_id}:laps until interrupted.

    Args:
        session_id: Session to replay.
        rate_per_second: Events published per second.
    Returns:
        None. Loops the session's lap list indefinitely; Ctrl+C to stop.
    """
    laps = asyncio.run(_fetch_laps(session_id))
    if not laps:
        logger.warning("No lap data for session %s — nothing to replay", session_id)
        return

    logger.info(
        "Replaying %d laps for session %s at %.1f events/sec (Ctrl+C to stop)",
        len(laps),
        session_id,
        rate_per_second,
    )
    channel = f"f1:telemetry:{session_id}:laps"
    client = redis.Redis.from_url(get_redis_settings().redis_url, decode_responses=True)
    interval = 1.0 / rate_per_second
    published = 0
    try:
        for lap in itertools.cycle(laps):
            client.publish(channel, json.dumps(lap))
            published += 1
            if published % 50 == 0:
                logger.info("Published %d events", published)
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("Stopped after publishing %d events", published)
    finally:
        client.close()


def main() -> None:
    args = _parse_args()
    replay(args.session_id, args.rate)


if __name__ == "__main__":
    main()
