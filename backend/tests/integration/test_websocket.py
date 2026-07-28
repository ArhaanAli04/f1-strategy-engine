"""Integration tests for /ws/telemetry/{session_id}: token-gated connect,
lap-completion pub/sub fan-out, and the active-connection gauge's lifecycle.
"""

import asyncio
import json
import uuid
from datetime import date
from typing import Any

import pytest
import redis as sync_redis
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.redis import RedisContainer

from backend.core.metrics import f1_active_websocket_connections
from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.services import telemetry_service
from backend.tests.integration.conftest import seed_via_test_client


def _seed_session(
    test_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> tuple[uuid.UUID, uuid.UUID]:
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
    seed_via_test_client(test_client, db_session_factory, circuit, race, session_row, driver)
    return session_row.id, driver.id


@pytest.mark.integration
def test_ws_connect_requires_valid_token(test_client: TestClient) -> None:
    """No ?token= at all — websocket_telemetry closes before accept() with the
    app-defined policy-violation code 4401 (see telemetry.py's
    _WS_POLICY_VIOLATION_CLOSE_CODE), not a literal WS code 403: WebSocket
    close codes aren't HTTP status codes, and 403 isn't a valid one — 4401 is
    this codebase's own convention for "auth/lookup failed the handshake."
    """
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with test_client.websocket_connect(f"/api/v1/ws/telemetry/{uuid.uuid4()}"):
            pass

    assert exc_info.value.code == 4401


@pytest.mark.integration
async def test_ws_receives_message_on_lap_completion(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    redis_container: RedisContainer,
) -> None:
    """Connect authenticated, publish a lap-completion event directly to
    Redis (bypassing the real ingestion pipeline, same "call the transport
    boundary directly" convention as test_telemetry_ingestion.py), and
    confirm the WS client receives the enriched LapCompletedEvent.

    async def + asyncio.wait_for(asyncio.to_thread(...)): the TestClient's
    websocket session's receive_text() is a blocking sync call (it proxies
    onto its own anyio portal thread) — to_thread hands it a thread so
    wait_for's timeout can actually apply instead of blocking indefinitely.
    Also must seed via seed_via_test_client rather than asyncio.run(), since
    this test's own event loop is already running (pytest-asyncio, see
    pyproject.toml's asyncio_mode = "auto") and a nested asyncio.run() would
    raise.
    """
    session_id, driver_id = _seed_session(authenticated_client, db_session_factory)
    access_token = authenticated_client.headers["Authorization"].split(" ", 1)[1]

    publisher = sync_redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
    )
    try:
        with authenticated_client.websocket_connect(
            f"/api/v1/ws/telemetry/{session_id}?token={access_token}"
        ) as websocket:
            lap_summary = {
                "driver_id": str(driver_id),
                "session_id": str(session_id),
                "lap_number": 12,
                "lap_time_seconds": 91.234,
                "compound": "MEDIUM",
                "sector1_seconds": 28.1,
                "sector2_seconds": 35.0,
                "sector3_seconds": 28.134,
            }
            publisher.publish(f"f1:telemetry:{session_id}:laps", json.dumps(lap_summary))

            raw = await asyncio.wait_for(asyncio.to_thread(websocket.receive_text), timeout=2.0)
    finally:
        publisher.close()

    envelope = json.loads(raw)
    assert envelope["event"] == "lap_completed"
    assert envelope["session_id"] == str(session_id)
    assert envelope["data"]["driver_id"] == str(driver_id)
    assert envelope["data"]["lap_number"] == 12
    assert envelope["data"]["compound"] == "MEDIUM"


@pytest.mark.integration
async def test_ws_fans_out_from_one_shared_broadcaster(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    redis_container: RedisContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two connections to the same session_id must share one _SessionBroadcaster:
    a single publish should reach both connections with the identical
    envelope, and get_live_car_channels — the per-message enrichment lookup —
    must be called exactly once, not once per connection. This is the core
    regression test for the WS fan-out fix (see CLAUDE.md's "WS telemetry
    broadcast: redundant per-connection enrichment fan-out" deferred-wiring
    entry): before the fix, each connection ran its own pubsub loop and
    independently re-ran this lookup on every event.
    """
    session_id, driver_id = _seed_session(authenticated_client, db_session_factory)
    access_token = authenticated_client.headers["Authorization"].split(" ", 1)[1]

    call_count = 0
    original_get_live_car_channels = telemetry_service.get_live_car_channels

    async def _counting_get_live_car_channels(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return await original_get_live_car_channels(*args, **kwargs)

    monkeypatch.setattr(telemetry_service, "get_live_car_channels", _counting_get_live_car_channels)

    publisher = sync_redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
    )
    try:
        with (
            authenticated_client.websocket_connect(
                f"/api/v1/ws/telemetry/{session_id}?token={access_token}"
            ) as ws_a,
            authenticated_client.websocket_connect(
                f"/api/v1/ws/telemetry/{session_id}?token={access_token}"
            ) as ws_b,
        ):
            lap_summary = {
                "driver_id": str(driver_id),
                "session_id": str(session_id),
                "lap_number": 7,
                "lap_time_seconds": 88.5,
                "compound": "HARD",
                "sector1_seconds": 29.0,
                "sector2_seconds": 30.0,
                "sector3_seconds": 29.5,
            }
            publisher.publish(f"f1:telemetry:{session_id}:laps", json.dumps(lap_summary))

            raw_a = await asyncio.wait_for(asyncio.to_thread(ws_a.receive_text), timeout=2.0)
            raw_b = await asyncio.wait_for(asyncio.to_thread(ws_b.receive_text), timeout=2.0)
    finally:
        publisher.close()

    assert raw_a == raw_b
    envelope = json.loads(raw_a)
    assert envelope["event"] == "lap_completed"
    assert envelope["data"]["driver_id"] == str(driver_id)
    assert envelope["data"]["lap_number"] == 7
    assert call_count == 1


@pytest.mark.integration
async def test_ws_disconnect_cleans_up_connection_gauge(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    redis_container: RedisContainer,
) -> None:
    """f1_active_websocket_connections is a global Gauge shared across the
    whole test session (see core/metrics.py's module-docstring rationale for
    one shared CollectorRegistry) — assert the before/after delta rather than
    an absolute value, since other tests' connections may have left it at any
    starting point.

    A publish+receive round trip happens inside the connected block before
    reading gauge_after_connect: websocket_telemetry's f1_..._connections.inc()
    runs strictly before pubsub.subscribe()/the forward task starts, and the
    client can only receive a forwarded message once that forward task is
    running — so a successful receive is proof .inc() already happened,
    avoiding a race against the client-side accept() handshake alone (whose
    completion doesn't guarantee the server coroutine's next line has run).
    """
    session_id, driver_id = _seed_session(authenticated_client, db_session_factory)
    access_token = authenticated_client.headers["Authorization"].split(" ", 1)[1]

    publisher = sync_redis.Redis(
        host=redis_container.get_container_host_ip(),
        port=int(redis_container.get_exposed_port(6379)),
    )
    gauge_before_connect = f1_active_websocket_connections._value.get()

    try:
        with authenticated_client.websocket_connect(
            f"/api/v1/ws/telemetry/{session_id}?token={access_token}"
        ) as websocket:
            lap_summary = {
                "driver_id": str(driver_id),
                "session_id": str(session_id),
                "lap_number": 1,
                "lap_time_seconds": 90.0,
                "compound": "SOFT",
                "sector1_seconds": 27.0,
                "sector2_seconds": 33.0,
                "sector3_seconds": 30.0,
            }
            publisher.publish(f"f1:telemetry:{session_id}:laps", json.dumps(lap_summary))
            await asyncio.wait_for(asyncio.to_thread(websocket.receive_text), timeout=2.0)

            gauge_after_connect = f1_active_websocket_connections._value.get()
    finally:
        publisher.close()

    # The server-side finally block (unsubscribe, then .dec()) runs on
    # websocket_telemetry's own asyncio task, asynchronously to the client's
    # __exit__ returning — a real Redis unsubscribe is a network round trip,
    # not instant. Poll briefly rather than asserting immediately. Note
    # .dec() no longer waits on pubsub.aclose() at all — see telemetry.py's
    # comment on why that's a detached background task — so this settles
    # quickly and doesn't need to tolerate any multi-second cleanup delay.
    for _ in range(20):
        gauge_after_disconnect = f1_active_websocket_connections._value.get()
        if gauge_after_disconnect == gauge_before_connect:
            break
        await asyncio.sleep(0.1)

    assert gauge_after_connect == gauge_before_connect + 1
    assert gauge_after_disconnect == gauge_before_connect
