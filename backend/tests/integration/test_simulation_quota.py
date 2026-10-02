"""Integration tests for the Strategy Simulator's daily scenario quotas (Day 5).

POST /strategy/{session_id}/simulate and GET /strategy/simulate/quota against
real Postgres and Redis. The Celery hop is stubbed (no simulation runs): what is
under test is the quota around it. Limits are set by patching the quota
service's settings; the defaults (unlimited) stay in force everywhere else.
"""

import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient
from kombu.exceptions import OperationalError as KombuOperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.redis import RedisContainer

from backend.core import fly_machines
from backend.services import simulation_quota_service
from backend.tests.integration.test_strategy_endpoint import _seed_session_with_lap
from backend.workers import prediction_worker

# Generated per run so no password literal is committed (secret scanners flag one).
PASSWORD = f"Qu0ta-{uuid.uuid4().hex[:12]}!"


@pytest.fixture
def _fresh_quota_keys(redis_container: RedisContainer) -> Generator[None, None, None]:
    """Today's global counter is shared by every test on this Redis container."""
    client = sync_redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
    )
    try:
        keys = list(client.scan_iter("f1:sim_quota:*"))
        if keys:
            client.delete(*keys)
        yield
    finally:
        client.close()


def _limits(monkeypatch: pytest.MonkeyPatch, per_user: int, global_: int) -> None:
    settings = MagicMock(sim_daily_scenarios_per_user=per_user, sim_daily_scenarios_global=global_)
    monkeypatch.setattr(simulation_quota_service, "get_app_settings", lambda: settings)


def _stub_enqueue(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    delay = MagicMock(return_value=MagicMock(id=str(uuid.uuid4()), status="PENDING"))
    monkeypatch.setattr(prediction_worker.run_race_simulation, "delay", delay)
    return delay


def _another_user(client: TestClient) -> dict[str, str]:
    email = f"quota-{uuid.uuid4()}@example.com"
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "full_name": "Quota Test User"},
    )
    token = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _plan(driver_id: uuid.UUID, scenarios: int = 1, current_lap: int = 12) -> dict[str, Any]:
    body: dict[str, Any] = {
        "driver_id": str(driver_id),
        "current_lap": current_lap,
        "current_compound": "MEDIUM",
        "current_tyre_age": 12,
        "remaining_laps": 30,
    }
    if scenarios > 1:
        body["scenarios"] = [
            {"pit_laps": [current_lap + 2 + i], "compounds": ["HARD"]} for i in range(scenarios)
        ]
    return body


def _simulate(
    client: TestClient,
    session_id: uuid.UUID,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(f"/api/v1/strategy/{session_id}/simulate", json=body, headers=headers)


@pytest.mark.integration
@pytest.mark.usefixtures("_fresh_quota_keys")
def test_the_sixth_scenario_of_the_day_is_refused_with_429(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limits(monkeypatch, 5, 15)
    delay = _stub_enqueue(monkeypatch)
    session_id, driver_id = _seed_session_with_lap(
        authenticated_client, db_session_factory, "MEDIUM"
    )

    assert _simulate(authenticated_client, session_id, _plan(driver_id, 4)).status_code == 202
    assert _simulate(authenticated_client, session_id, _plan(driver_id)).status_code == 202
    refused = _simulate(authenticated_client, session_id, _plan(driver_id))

    assert refused.status_code == 429
    body = refused.json()
    assert body["error"] == "QUOTA_EXCEEDED"
    assert body["detail"]["scope"] == "user"
    assert body["detail"]["remaining"] == 0
    assert 0 < int(refused.headers["Retry-After"]) <= 24 * 60 * 60
    assert delay.call_count == 2  # the refused run was never queued


@pytest.mark.integration
@pytest.mark.usefixtures("_fresh_quota_keys")
def test_the_sixteenth_scenario_of_the_day_is_refused_for_another_user(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limits(monkeypatch, 5, 15)
    _stub_enqueue(monkeypatch)
    session_id, driver_id = _seed_session_with_lap(
        authenticated_client, db_session_factory, "MEDIUM"
    )
    for _ in range(3):  # three users use their full 5 each: 15 for the site
        headers = _another_user(authenticated_client)
        assert (
            _simulate(authenticated_client, session_id, _plan(driver_id, 4), headers).status_code
            == 202
        )
        assert (
            _simulate(authenticated_client, session_id, _plan(driver_id), headers).status_code
            == 202
        )

    refused = _simulate(authenticated_client, session_id, _plan(driver_id))  # a fresh user

    assert refused.status_code == 429
    assert refused.json()["detail"]["scope"] == "global"


@pytest.mark.integration
@pytest.mark.usefixtures("_fresh_quota_keys")
def test_quota_endpoint_reports_used_and_remaining(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limits(monkeypatch, 5, 15)
    _stub_enqueue(monkeypatch)
    session_id, driver_id = _seed_session_with_lap(
        authenticated_client, db_session_factory, "MEDIUM"
    )
    other = _another_user(authenticated_client)
    _simulate(authenticated_client, session_id, _plan(driver_id, 3))
    _simulate(authenticated_client, session_id, _plan(driver_id), other)

    response = authenticated_client.get("/api/v1/strategy/simulate/quota")

    assert response.status_code == 200
    body = response.json()
    assert body["day"] == datetime.now(UTC).date().isoformat()
    assert body["user_quota"] == {"limit": 5, "used": 3, "remaining": 2}
    assert body["global_quota"] == {"limit": 15, "used": 4, "remaining": 11}


@pytest.mark.integration
@pytest.mark.usefixtures("_fresh_quota_keys")
def test_a_request_that_fails_validation_uses_no_quota(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limits(monkeypatch, 5, 15)
    _stub_enqueue(monkeypatch)
    session_id, driver_id = _seed_session_with_lap(
        authenticated_client, db_session_factory, "MEDIUM"
    )

    beyond_progress = _simulate(authenticated_client, session_id, _plan(driver_id, current_lap=68))
    unknown_session = _simulate(authenticated_client, uuid.uuid4(), _plan(driver_id))

    assert beyond_progress.status_code == 422
    assert unknown_session.status_code == 404
    quota = authenticated_client.get("/api/v1/strategy/simulate/quota").json()
    assert quota["user_quota"]["used"] == 0
    assert quota["global_quota"]["used"] == 0


@pytest.mark.integration
@pytest.mark.usefixtures("_fresh_quota_keys")
def test_a_run_that_cannot_be_queued_gives_its_quota_back(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limits(monkeypatch, 5, 15)
    monkeypatch.setattr(
        prediction_worker.run_race_simulation,
        "delay",
        MagicMock(side_effect=KombuOperationalError("broker unreachable")),
    )
    session_id, driver_id = _seed_session_with_lap(
        authenticated_client, db_session_factory, "MEDIUM"
    )

    with pytest.raises(KombuOperationalError):
        _simulate(authenticated_client, session_id, _plan(driver_id, 2))

    quota = authenticated_client.get("/api/v1/strategy/simulate/quota").json()
    assert quota["user_quota"]["used"] == 0
    assert quota["global_quota"]["used"] == 0


@pytest.mark.integration
@pytest.mark.usefixtures("_fresh_quota_keys")
def test_without_limits_configured_the_quota_is_unlimited(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limits(monkeypatch, 0, 0)
    _stub_enqueue(monkeypatch)
    session_id, driver_id = _seed_session_with_lap(
        authenticated_client, db_session_factory, "MEDIUM"
    )
    for _ in range(7):
        assert _simulate(authenticated_client, session_id, _plan(driver_id)).status_code == 202

    body = authenticated_client.get("/api/v1/strategy/simulate/quota").json()

    assert body["user_quota"] == {"limit": None, "used": 0, "remaining": None}


@pytest.mark.integration
def test_quota_endpoint_requires_sign_in(test_client: TestClient) -> None:
    assert test_client.get("/api/v1/strategy/simulate/quota").status_code == 401


# --- Starting the worker machine on demand (Day 6, core/fly_machines.py) ---


@pytest.mark.integration
@pytest.mark.usefixtures("_fresh_quota_keys")
def test_only_a_queued_run_asks_fly_to_start_the_worker(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limits(monkeypatch, 1, 15)
    _stub_enqueue(monkeypatch)
    start = AsyncMock(return_value=fly_machines.WorkerStart.STARTED)
    monkeypatch.setattr(fly_machines, "ensure_worker_started", start)
    session_id, driver_id = _seed_session_with_lap(
        authenticated_client, db_session_factory, "MEDIUM"
    )

    invalid = _simulate(authenticated_client, session_id, _plan(driver_id, current_lap=68))
    accepted = _simulate(authenticated_client, session_id, _plan(driver_id))
    refused = _simulate(authenticated_client, session_id, _plan(driver_id))

    assert (invalid.status_code, accepted.status_code, refused.status_code) == (422, 202, 429)
    start.assert_awaited_once()


@pytest.mark.integration
@pytest.mark.usefixtures("_fresh_quota_keys")
def test_a_failed_worker_start_still_queues_the_run_and_keeps_its_quota(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _limits(monkeypatch, 5, 15)
    delay = _stub_enqueue(monkeypatch)
    # The real helper, switched on, pointed at a port nothing listens on.
    fly_settings = MagicMock(
        fly_worker_autostart=True,
        fly_api_token="fly-token-for-tests",  # noqa: S106 — sent nowhere reachable
        fly_app_name="f1-strategy",
        fly_api_hostname="http://127.0.0.1:9",
        fly_worker_process_group="worker",
    )
    monkeypatch.setattr(fly_machines, "get_app_settings", lambda: fly_settings)
    session_id, driver_id = _seed_session_with_lap(
        authenticated_client, db_session_factory, "MEDIUM"
    )

    response = _simulate(authenticated_client, session_id, _plan(driver_id))

    assert response.status_code == 202
    delay.assert_called_once()
    quota = authenticated_client.get("/api/v1/strategy/simulate/quota").json()
    assert quota["user_quota"]["used"] == 1  # not refunded: the task is queued
