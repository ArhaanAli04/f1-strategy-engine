"""Integration test for GET /telemetry/{session_id}/gaps against real Postgres and Redis.

The live ingestor and Demo Replay playback write the timing tower to
f1:{season}:{round}:gaps; the endpoint must hand it to clients unchanged,
including each car's current tyre (demo deployment Day 6b), which the towers
use to show a new tyre from the pit stop instead of a lap later.
"""

import json
import uuid
from collections.abc import Generator
from datetime import date

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.redis import RedisContainer

from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.tests.integration.conftest import seed_via_test_client

# A season no real data uses, so this key cannot collide with other tests'.
SEASON, ROUND = 2098, 1
GAPS_KEY = f"f1:{SEASON}:{ROUND}:gaps"


@pytest.fixture
def redis_client(redis_container: RedisContainer) -> Generator[sync_redis.Redis, None, None]:  # type: ignore[type-arg]
    client = sync_redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
    )
    try:
        yield client
    finally:
        client.delete(GAPS_KEY)
        client.close()


@pytest.fixture
def session_id(
    test_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> uuid.UUID:
    circuit = Circuit(id=uuid.uuid4(), name="Gaps Test Circuit", country="X", track_length_km=5.0)
    race = Race(
        id=uuid.uuid4(),
        season=SEASON,
        round_number=ROUND,
        circuit_id=circuit.id,
        race_date=date(2098, 3, 1),
        status="in_progress",
    )
    session = SessionModel(
        id=uuid.uuid4(), race_id=race.id, session_type="R", session_date=date(2098, 3, 1)
    )
    seed_via_test_client(test_client, db_session_factory, circuit, race, session)
    return session.id


@pytest.mark.integration
def test_gaps_endpoint_returns_each_cars_current_tyre(
    test_client: TestClient,
    redis_client: sync_redis.Redis,  # type: ignore[type-arg]
    session_id: uuid.UUID,
) -> None:
    pitted, other = str(uuid.uuid4()), str(uuid.uuid4())
    entry = {
        "lap_number": 18,
        "gap_to_behind_seconds": 0.0,
        "laps_behind": 0,
    }
    tower = {
        "session_id": str(session_id),
        "source": "live",
        "gaps": [
            {**entry, "driver_id": other, "position": 1, "gap_to_ahead_seconds": 0.0},
            {
                **entry,
                "driver_id": pitted,
                "position": 2,
                "gap_to_ahead_seconds": 4.2,
                "compound": "HARD",
            },
        ],
    }
    redis_client.setex(GAPS_KEY, 30, json.dumps(tower))

    response = test_client.get(f"/api/v1/telemetry/{session_id}/gaps")

    assert response.status_code == 200
    gaps = {g["driver_id"]: g for g in response.json()["gaps"]}
    assert gaps[pitted]["compound"] == "HARD"
    assert gaps[other]["compound"] is None  # older payloads without the field still work
