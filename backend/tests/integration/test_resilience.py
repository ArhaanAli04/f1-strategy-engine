"""Day 38 resilience/failure-mode tests — Redis, DB, ML model, and Celery config.

Placed under tests/integration/ per Day 38's spec (so it inherits the
containerized Postgres/Redis autouse fixtures from conftest.py), even though
only test_db_connection_failure_returns_503 actually needs a real container —
the other three exercise pure function/config behavior with mocks and are
still tagged @pytest.mark.resilience rather than @pytest.mark.integration.
"""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, Mock, patch

import fakeredis as fakeredis_lib
import numpy as np
import pytest
from fastapi.testclient import TestClient
from redis.exceptions import RedisError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services import cache_service
from backend.workers import prediction_worker


@pytest.mark.resilience
async def test_cache_get_returns_none_on_redis_exception(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """A Redis-down failure inside cache_get must degrade to a cache miss (None),
    not propagate — every existing caller (including cacheable()) already
    treats None as "go compute it", which is the correct degraded behavior
    when Redis itself is unreachable.
    """
    fakeredis.get = AsyncMock(  # type: ignore[method-assign]
        side_effect=RedisError("mock Redis connection refused")
    )

    result = await cache_service.cache_get(fakeredis, "f1:test:key")

    assert result is None


@pytest.mark.resilience
def test_db_connection_failure_returns_503(authenticated_client: TestClient) -> None:
    """GET /alerts requires auth (JWT-only, no DB) but its body hits the DB via
    get_db. Forcing get_db itself to fail simulates a real connection-pool/DB
    outage and must surface as 503 + Retry-After — not the generic 500
    unhandled_error_handler would otherwise produce — per
    core/exceptions.py's db_connection_error_handler (Checkpoint 2).

    db_connection_error_handler logs via logger.error(..., exc_info=True) —
    SENTRY_DSN is a real, live DSN in this repo's .env (read directly by
    AppSettings' env_file, not just os.environ), so TestClient's real
    lifespan genuinely calls sentry_sdk.init() and Sentry's LoggingIntegration
    would turn that ERROR-level log into a real captured event (confirmed:
    its EventHandler._emit calls the module-level sentry_sdk.capture_event
    directly). Patching both capture_exception and capture_event for the
    duration of this intentionally-triggered failure keeps a deliberate test
    scenario from paging/alerting the real Sentry project.
    """
    from backend.core.database import get_db
    from backend.main import app as fastapi_app

    async def _raise_operational_error() -> AsyncGenerator[AsyncSession, None]:
        raise OperationalError("SELECT 1", {}, Exception("mock connection refused"))
        yield  # pragma: no cover — unreachable, required for generator typing

    fastapi_app.dependency_overrides[get_db] = _raise_operational_error
    try:
        with patch("sentry_sdk.capture_exception"), patch("sentry_sdk.capture_event"):
            response = authenticated_client.get("/api/v1/alerts")
    finally:
        del fastapi_app.dependency_overrides[get_db]

    assert response.status_code == 503
    assert response.headers.get("Retry-After") == "30"
    assert response.json()["error"] == "DATABASE_UNAVAILABLE"


@pytest.mark.resilience
def test_prediction_worker_continues_on_model_exception() -> None:
    """model.predict() raising (corrupted weights, a shape mismatch, etc.) must
    not crash the Celery worker — _run_inference should catch it, report it to
    Sentry, and fall back to the same null-prediction defaults already used
    when a model never loaded at all (Checkpoint 3).

    Unlike test_db_connection_failure_returns_503 above, the Sentry capture
    here is NOT suppressed — it's the exact behavior under test (the
    assert on mock_capture below). patch("sentry_sdk.capture_exception")
    still means no real event reaches the live Sentry project (it replaces
    the real function for the duration of the `with` block, same mechanism
    as the suppression above), so this intentionally-triggered failure is
    also safe to run against the real .env SENTRY_DSN — the patch here
    exists to assert the call happened, not to hide it.
    """
    driver_id = uuid.uuid4()
    lap_number = 20
    context = {
        "session_id": str(uuid.uuid4()),
        "driver_id": str(driver_id),
        "compound": "MEDIUM",
        "lap_number": lap_number,
        "tyre_age_laps": 10,
    }
    resolved = {
        "circuit_id": uuid.uuid4(),
        "circuit_name": "Test Circuit",
        "season": 2026,
        "round_number": 1,
        "total_laps": 50,
        "track_temp": 35.0,
        "air_temp": 25.0,
        "position": 5,
        "gap_to_car_ahead": 3.0,
        "gap_to_car_behind": 4.0,
        "target_ahead_driver_id": None,
        "target_behind_driver_id": None,
    }
    broken_deg_model = Mock()
    broken_deg_model.predict.side_effect = RuntimeError("corrupted tire_deg model weights")
    models = {
        "tire_deg_medium.pkl": broken_deg_model,
        "pit_predictor.pkl": Mock(predict_proba=Mock(return_value=np.array([[0.4, 0.6]]))),
        "safety_car_model.pkl": Mock(probability_within=Mock(return_value=0.1)),
    }

    with patch("sentry_sdk.capture_exception") as mock_capture:
        result = prediction_worker._run_inference(models, {}, context, resolved, driver_id)

    assert result["tire_life_remaining"] == 0.0
    assert result["optimal_pit_lap"] == lap_number + 1
    assert result["pit_probability"] == pytest.approx(0.6)
    mock_capture.assert_called_once()
    call_args = mock_capture.call_args
    assert call_args is not None
    assert isinstance(call_args.args[0], RuntimeError)


@pytest.mark.resilience
def test_celery_task_configuration_has_acks_late() -> None:
    """Guards Checkpoint 4's Celery re-queue settings against silent regression."""
    from backend.workers.celery_app import app as celery_app

    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
