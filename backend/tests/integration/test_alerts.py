"""Integration tests for alert dispatch and the GET/PUT /alerts routes.

test_undercut_alert_created_in_db calls alert_service.evaluate_threats
directly (no HTTP, no Celery) against a real DB + Redis — mirrors the
"call the service/task directly, skip the transport" convention already used
by test_telemetry_ingestion.py and test_live_prediction_pipeline.py.
alert_service.UNDERCUT_ALERT_THRESHOLD is actually 0.5 in code (not the 0.65
the Day 17 spec note mentioned) — seeding undercut_score=0.75 clears both
values regardless, so the test is correct either way.

test_alert_appears_in_user_get_alerts / test_mark_alert_read seed an Alert
row directly (bypassing evaluate_threats/dispatch_alert, which are already
covered above) to isolate what's actually under test: the GET/PUT /alerts
routes and alert_service.get_user_alerts/mark_alert_read.
"""

import asyncio
import os
import uuid
from datetime import UTC, date, datetime

import pytest
import redis.asyncio as aioredis
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData
from backend.models.user import Alert, Subscription, User
from backend.services import alert_service
from backend.tests.integration.conftest import seed_via_test_client


@pytest.mark.integration
def test_undercut_alert_created_in_db(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
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
    driver_ahead = Driver(
        id=uuid.uuid4(), code="HAM", full_name="Lewis Hamilton", nationality="GBR"
    )
    driver_trailing = Driver(
        id=uuid.uuid4(), code="VER", full_name="Max Verstappen", nationality="NED"
    )
    user = User(
        id=uuid.uuid4(),
        email=f"alertee-{uuid.uuid4()}@example.com",
        hashed_password="not-a-real-hash",  # noqa: S106 — evaluate_threats never checks this
        full_name="Alert Test User",
    )
    lap_ahead = LapData(
        id=uuid.uuid4(),
        session_id=session_row.id,
        driver_id=driver_ahead.id,
        lap_number=10,
        compound="MEDIUM",
        tyre_age_laps=10,
        position=1,
    )
    lap_trailing = LapData(
        id=uuid.uuid4(),
        session_id=session_row.id,
        driver_id=driver_trailing.id,
        lap_number=10,
        compound="MEDIUM",
        tyre_age_laps=10,
        position=2,
    )
    # Above alert_service.UNDERCUT_ALERT_THRESHOLD (0.5, not the 0.65 the
    # spec note mentioned — see module docstring).
    prediction = StrategyPrediction(
        id=uuid.uuid4(),
        session_id=session_row.id,
        driver_id=driver_trailing.id,
        predicted_at=datetime.now(UTC),
        optimal_pit_lap=20,
        pit_probability=0.5,
        undercut_score=0.75,
        overcut_score=0.1,
        tire_life_remaining=5.0,
        confidence_score=0.8,
        model_version="test",
    )
    subscription = Subscription(
        id=uuid.uuid4(),
        user_id=user.id,
        driver_ids=[str(driver_trailing.id)],
        team_ids=[],
        alert_types=["UNDERCUT_THREAT"],
    )

    async def _seed() -> None:
        async with db_session_factory() as db:
            db.add_all(
                [
                    circuit,
                    race,
                    session_row,
                    driver_ahead,
                    driver_trailing,
                    user,
                    lap_ahead,
                    lap_trailing,
                    prediction,
                    subscription,
                ]
            )
            await db.commit()
        await get_engine().dispose()

    asyncio.run(_seed())

    async def _evaluate() -> list[dict[str, object]]:
        # A dedicated client scoped to this asyncio.run() call, rather than
        # the shared core/redis_client.py pool singleton, which may already
        # be bound to a different (test_client portal, or an earlier
        # asyncio.run()) event loop by this point in the session — see
        # db_session_factory's docstring for the same cross-loop hazard.
        redis_client = aioredis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
        try:
            async with db_session_factory() as db:
                return await alert_service.evaluate_threats(db, redis_client, session_row.id)
        finally:
            await redis_client.aclose()  # type: ignore[attr-defined]
            # Dispose before this asyncio.run() call returns and closes its
            # loop — otherwise the connection(s) opened above by
            # db_session_factory() are left pooled and bound to a loop that's
            # about to be torn down, and _assert_persisted's own asyncio.run()
            # (a new loop) can be handed one of them (same hazard
            # db_session_factory's own docstring documents).
            await get_engine().dispose()

    dispatched = asyncio.run(_evaluate())

    assert len(dispatched) == 1
    assert dispatched[0]["driver_id"] == str(driver_trailing.id)

    async def _assert_persisted() -> None:
        async with db_session_factory() as db:
            result = await db.execute(select(Alert).where(Alert.session_id == session_row.id))
            alert = result.scalar_one()
            assert alert.user_id == user.id
            assert alert.alert_type == "UNDERCUT_THREAT"
            assert alert.driver_id == driver_trailing.id
        await get_engine().dispose()

    asyncio.run(_assert_persisted())


def _seed_alert_for_authenticated_user(
    test_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed one Alert row belonging to the currently-authenticated user.

    Returns:
        (alert_id, session_id).
    """
    me_response = test_client.get("/api/v1/auth/me")
    user_id = uuid.UUID(me_response.json()["id"])

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
    alert = Alert(
        id=uuid.uuid4(),
        user_id=user_id,
        session_id=session_row.id,
        alert_type="UNDERCUT_THREAT",
        message="Undercut threat: driver X on driver Y (75%)",
        triggered_at=datetime.now(UTC),
    )
    seed_via_test_client(test_client, db_session_factory, circuit, race, session_row, alert)
    return alert.id, session_row.id


@pytest.mark.integration
def test_alert_appears_in_user_get_alerts(
    authenticated_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alert_id, session_id = _seed_alert_for_authenticated_user(
        authenticated_client, db_session_factory
    )

    response = authenticated_client.get("/api/v1/alerts")

    assert response.status_code == 200
    body = response.json()
    assert any(entry["id"] == str(alert_id) for entry in body)
    matched = next(entry for entry in body if entry["id"] == str(alert_id))
    assert matched["session_id"] == str(session_id)
    assert matched["read_at"] is None


@pytest.mark.integration
def test_mark_alert_read(
    authenticated_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alert_id, _session_id = _seed_alert_for_authenticated_user(
        authenticated_client, db_session_factory
    )

    response = authenticated_client.put(f"/api/v1/alerts/{alert_id}/read")

    assert response.status_code == 200
    assert response.json()["read_at"] is not None

    refetch = authenticated_client.get("/api/v1/alerts")
    matched = next(entry for entry in refetch.json() if entry["id"] == str(alert_id))
    assert matched["read_at"] is not None
