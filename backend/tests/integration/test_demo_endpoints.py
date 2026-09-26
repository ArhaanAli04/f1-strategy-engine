"""Integration tests for the /demo/replay/* control endpoints.

The replay subprocess launch and os.kill are monkeypatched out (see
no_real_subprocess) — these tests exercise the HTTP + auth + Redis-state path,
not a real replay_pipeline.py process.
"""

import json
import os
import uuid
from datetime import date

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.redis import RedisContainer

from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.services import demo_service
from backend.tests.integration.conftest import seed_via_test_client


@pytest.fixture
def british_gp_session_id(
    test_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> str:
    """Seed the British GP 2026 (round 9) R session — one of the curated races.

    Its id is generated here, like any real ingest, so the endpoints must find
    it by season and round rather than by a known id.
    """
    circuit = Circuit(
        id=uuid.uuid4(), name="Silverstone Circuit", country="UK", track_length_km=5.891
    )
    race = Race(
        id=uuid.uuid4(),
        season=2026,
        round_number=9,
        circuit_id=circuit.id,
        race_date=date(2026, 7, 5),
        status="completed",
    )
    session = SessionModel(
        id=uuid.uuid4(), race_id=race.id, session_type="R", session_date=date(2026, 7, 5)
    )
    seed_via_test_client(test_client, db_session_factory, circuit, race, session)
    return str(session.id)


class _FakeProc:
    def __init__(self, pid: int = 5151) -> None:
        self.pid = pid


@pytest.fixture
def no_real_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_launch(session_id: uuid.UUID, start_lap: int, end_lap: int) -> _FakeProc:
        return _FakeProc()

    monkeypatch.setattr(demo_service, "_launch_replay_subprocess", _fake_launch)
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    # The fake PID is not a real process — keep get_replay_status from
    # self-healing the state away mid-test.
    monkeypatch.setattr(demo_service, "_process_is_alive", lambda _pid: True)


def _set_live_gaps_key(redis_container: RedisContainer) -> None:
    """Simulate a live ingestor's gaps publish — the "source": "live" marker
    is what detect_live_race keys off (not TTL, and not a bare payload)."""
    client = sync_redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
    )
    try:
        client.setex(
            "f1:2026:10:gaps", 30, json.dumps({"session_id": "s", "gaps": [], "source": "live"})
        )
    finally:
        client.close()


@pytest.mark.integration
def test_sessions_endpoint_is_public_and_lists_db_session_id(
    test_client: TestClient, british_gp_session_id: str
) -> None:
    resp = test_client.get("/api/v1/demo/sessions")
    assert resp.status_code == 200
    # Only the British GP is ingested in this database; the other two
    # curated races are left out.
    sessions = resp.json()["sessions"]
    assert [s["session_id"] for s in sessions] == [british_gp_session_id]
    assert sessions[0]["race_name"] == "British Grand Prix 2026"


@pytest.mark.integration
def test_sessions_endpoint_empty_when_no_curated_race_ingested(test_client: TestClient) -> None:
    resp = test_client.get("/api/v1/demo/sessions")
    assert resp.status_code == 200
    assert resp.json()["sessions"] == []


@pytest.mark.integration
def test_replay_available_true_on_clean_state(test_client: TestClient) -> None:
    resp = test_client.get("/api/v1/demo/replay/available")
    assert resp.status_code == 200
    assert resp.json()["available"] is True


@pytest.mark.integration
def test_replay_available_false_with_live_gaps_key(
    test_client: TestClient, redis_container: RedisContainer
) -> None:
    _set_live_gaps_key(redis_container)
    resp = test_client.get("/api/v1/demo/replay/available")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["reason"]


@pytest.mark.integration
def test_start_requires_auth(test_client: TestClient, british_gp_session_id: str) -> None:
    resp = test_client.post("/api/v1/demo/replay/start", json={"session_id": british_gp_session_id})
    assert resp.status_code == 401


@pytest.mark.integration
def test_start_rejects_non_curated_session(
    authenticated_client: TestClient, no_real_subprocess: None
) -> None:
    resp = authenticated_client.post(
        "/api/v1/demo/replay/start", json={"session_id": str(uuid.uuid4())}
    )
    assert resp.status_code == 422


@pytest.mark.integration
def test_start_conflicts_with_live_race(
    authenticated_client: TestClient,
    redis_container: RedisContainer,
    no_real_subprocess: None,
    british_gp_session_id: str,
) -> None:
    _set_live_gaps_key(redis_container)
    resp = authenticated_client.post(
        "/api/v1/demo/replay/start", json={"session_id": british_gp_session_id}
    )
    assert resp.status_code == 409


@pytest.mark.integration
def test_stop_404_when_nothing_running(authenticated_client: TestClient) -> None:
    resp = authenticated_client.post("/api/v1/demo/replay/stop")
    assert resp.status_code == 404


@pytest.mark.integration
def test_start_status_stop_roundtrip(
    authenticated_client: TestClient, no_real_subprocess: None, british_gp_session_id: str
) -> None:
    start = authenticated_client.post(
        "/api/v1/demo/replay/start", json={"session_id": british_gp_session_id}
    )
    assert start.status_code == 202
    assert start.json()["start_lap"] == 43

    status = authenticated_client.get("/api/v1/demo/replay/status")
    assert status.status_code == 200
    assert status.json()["running"] is True
    assert status.json()["session_id"] == british_gp_session_id

    dupe = authenticated_client.post(
        "/api/v1/demo/replay/start", json={"session_id": british_gp_session_id}
    )
    assert dupe.status_code == 409

    stop = authenticated_client.post("/api/v1/demo/replay/stop")
    assert stop.status_code == 200
    assert stop.json()["stopped"] is True

    after = authenticated_client.get("/api/v1/demo/replay/status")
    assert after.json()["running"] is False
