"""Integration tests for telemetry ingestion: the Celery lap-persistence path
and the live-timing Redis cache-population path.

Per CLAUDE.md's Day 16 spec note, no real Celery worker is started for
test_lap_written_to_db_after_celery_task — process_lap.delay(...).get() is
called directly with Celery in eager mode, matching the existing pattern in
test_live_prediction_pipeline.py.

test_redis_cache_populated_after_ingestion covers a DIFFERENT component than
the Celery task: f1:{season}:{round}:car:{car_number}:latest is written by
F1SignalRIngestor._handle_car_data in scripts/ingest_live_session.py, a plain
method on the live-timing ingestor class — not by process_lap or any Celery
task. That class normally receives this payload over a live WebSocket
connection to F1's timing feed; this test instantiates it directly and calls
_handle_car_data with a manually gzip+base64-encoded payload (matching
_decode_z's expected wire format), the same "call the function directly,
skip the transport" principle the spec applies to Celery tasks.
"""

import asyncio
import base64
import json
import uuid
import zlib
from datetime import date

import pytest
import redis as sync_redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.redis import RedisContainer

from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData
from backend.scripts.ingest_live_session import F1SignalRIngestor
from backend.workers.celery_app import app as celery_app
from backend.workers.telemetry_worker import process_lap


@pytest.fixture
def _eager_celery() -> None:
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True


@pytest.mark.integration
@pytest.mark.usefixtures("_eager_celery")
def test_lap_written_to_db_after_celery_task(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """publish (via .delay(), eager) -> process_lap persists -> row exists in lap_data."""
    circuit = Circuit(id=uuid.uuid4(), name="Test Circuit", country="Testland", track_length_km=5.0)
    race = Race(
        id=uuid.uuid4(),
        season=2025,
        round_number=1,
        circuit_id=circuit.id,
        race_date=date(2025, 3, 1),
        status="in_progress",
    )
    session_row = SessionModel(
        id=uuid.uuid4(), race_id=race.id, session_type="R", session_date=date(2025, 3, 1)
    )
    driver = Driver(id=uuid.uuid4(), code="VER", full_name="Max Verstappen", nationality="NED")

    async def _seed() -> None:
        async with db_session_factory() as db:
            db.add_all([circuit, race, session_row, driver])
            await db.commit()
        # asyncio.run() (below) gets its own event loop each call — dispose
        # so nothing pooled here survives into the task's own separately
        # asyncio.run()'d session usage. Same convention as
        # test_live_prediction_pipeline.py / db_session_factory's docstring.
        await get_engine().dispose()

    asyncio.run(_seed())

    raw_lap = {
        "session_id": str(session_row.id),
        "driver_id": str(driver.id),
        "lap_number": 15,
        "lap_time_seconds": 89.456,
        "compound": "HARD",
        "tyre_age_laps": 5,
        "is_valid": True,
        "sector1_seconds": 27.5,
        "sector2_seconds": 34.1,
        "sector3_seconds": 27.856,
    }

    process_lap.delay(raw_lap).get()

    async def _assert_persisted() -> None:
        async with db_session_factory() as db:
            result = await db.execute(
                select(LapData).where(
                    LapData.session_id == session_row.id,
                    LapData.driver_id == driver.id,
                    LapData.lap_number == 15,
                )
            )
            lap = result.scalar_one()
            assert lap.compound == "HARD"
            assert lap.lap_time_seconds == pytest.approx(89.456)
        await get_engine().dispose()

    asyncio.run(_assert_persisted())


def _encode_z(payload: dict[str, object]) -> str:
    """Gzip-over-base64-encode a dict, matching ingest_live_session._decode_z's
    expected wire format (raw deflate — no zlib header/trailer — then base64).
    """
    compressor = zlib.compressobj(level=6, method=zlib.DEFLATED, wbits=-zlib.MAX_WBITS)
    raw = compressor.compress(json.dumps(payload).encode()) + compressor.flush()
    return base64.b64encode(raw).decode()


@pytest.mark.integration
def test_redis_cache_populated_after_ingestion(redis_container: RedisContainer) -> None:
    """F1SignalRIngestor._handle_car_data writes f1:{season}:{round}:car:{car}:latest
    with an 8-second TTL, called directly (no live WebSocket connection).
    """
    client = sync_redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
        decode_responses=True,
    )
    try:
        ingestor = F1SignalRIngestor(
            season=2025,
            round_number=1,
            session_id=uuid.uuid4(),
            car_number_to_driver_id={},
            redis_client=client,
            no_auth=True,
        )
        car_entry = {"Channels": {"2": 312.5, "3": 8, "4": 100, "5": 0, "45": 8}}
        payload = _encode_z({"Cars": {"44": car_entry}})

        ingestor._handle_car_data(payload)

        key = "f1:2025:1:car:44:latest"
        cached_raw = client.get(key)
        assert cached_raw is not None
        assert json.loads(cached_raw) == car_entry

        ttl = client.ttl(key)
        assert 0 < ttl <= 8
    finally:
        client.close()
