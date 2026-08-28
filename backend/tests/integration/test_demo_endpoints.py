"""Integration tests for the /demo/replay/* control endpoints.

The replay subprocess launch and os.kill are monkeypatched out (see
no_real_subprocess) — these tests exercise the HTTP + auth + Redis-state path,
not a real replay_pipeline.py process.
"""

import json
import os
import uuid

import pytest
import redis as sync_redis
from fastapi.testclient import TestClient
from testcontainers.redis import RedisContainer

from backend.services import demo_service

_BRITISH_GP = "7da820bf-5e8c-49bb-b19f-cdd88325af87"


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
def test_sessions_endpoint_is_public(test_client: TestClient) -> None:
    resp = test_client.get("/api/v1/demo/sessions")
    assert resp.status_code == 200
    assert len(resp.json()["sessions"]) == 3


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
def test_start_requires_auth(test_client: TestClient) -> None:
    resp = test_client.post("/api/v1/demo/replay/start", json={"session_id": _BRITISH_GP})
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
) -> None:
    _set_live_gaps_key(redis_container)
    resp = authenticated_client.post("/api/v1/demo/replay/start", json={"session_id": _BRITISH_GP})
    assert resp.status_code == 409


@pytest.mark.integration
def test_stop_404_when_nothing_running(authenticated_client: TestClient) -> None:
    resp = authenticated_client.post("/api/v1/demo/replay/stop")
    assert resp.status_code == 404


@pytest.mark.integration
def test_start_status_stop_roundtrip(
    authenticated_client: TestClient, no_real_subprocess: None
) -> None:
    start = authenticated_client.post("/api/v1/demo/replay/start", json={"session_id": _BRITISH_GP})
    assert start.status_code == 202
    assert start.json()["start_lap"] == 43

    status = authenticated_client.get("/api/v1/demo/replay/status")
    assert status.status_code == 200
    assert status.json()["running"] is True
    assert status.json()["session_id"] == _BRITISH_GP

    dupe = authenticated_client.post("/api/v1/demo/replay/start", json={"session_id": _BRITISH_GP})
    assert dupe.status_code == 409

    stop = authenticated_client.post("/api/v1/demo/replay/stop")
    assert stop.status_code == 200
    assert stop.json()["stopped"] is True

    after = authenticated_client.get("/api/v1/demo/replay/status")
    assert after.json()["running"] is False
