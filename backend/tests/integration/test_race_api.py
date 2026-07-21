"""Integration tests for GET /races and GET /races/{id} against a real DB + Redis.

Also covers the public/authenticated API boundary: races.py has no auth-gated
routes at all (confirmed against every apis/v1/*.py — only auth.py's /me,
/fcm-token, /logout and all of alerts.py carry Depends(get_current_user)), so
the boundary test here targets GET /alerts instead of a race route.
"""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.models.race import Circuit, Race
from backend.tests.integration.conftest import seed_via_test_client


def _seed_races(n: int) -> tuple[list[Race], Circuit]:
    circuit = Circuit(id=uuid.uuid4(), name="Test Circuit", country="Testland", track_length_km=5.0)
    races = [
        Race(
            id=uuid.uuid4(),
            season=2025,
            round_number=round_number,
            circuit_id=circuit.id,
            race_date=date(2025, 1, round_number),
            status="completed",
        )
        for round_number in range(1, n + 1)
    ]
    return races, circuit


@pytest.mark.integration
def test_get_races_returns_paginated_list(
    test_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    races, circuit = _seed_races(5)
    seed_via_test_client(test_client, db_session_factory, circuit, *races)

    response = test_client.get("/api/v1/races", params={"season": 2025, "page_size": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5


@pytest.mark.integration
def test_get_race_by_id_returns_correct_circuit(
    test_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    races, circuit = _seed_races(1)
    race = races[0]
    seed_via_test_client(test_client, db_session_factory, circuit, race)

    response = test_client.get(f"/api/v1/races/{race.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["circuit"] is not None
    assert body["circuit"]["id"] == str(circuit.id)
    assert body["circuit"]["name"] == "Test Circuit"


@pytest.mark.integration
def test_race_endpoints_are_publicly_accessible(test_client: TestClient) -> None:
    """races.py has no auth-gated routes by design — a client can list/read
    race data without a token. This asserts that intentional design rather
    than a 401, which nothing in races.py would ever produce.
    """
    response = test_client.get("/api/v1/races")

    assert response.status_code == 200


@pytest.mark.integration
def test_alerts_endpoint_requires_auth(test_client: TestClient) -> None:
    """GET /alerts carries Depends(get_current_user) — the actual auth-gated
    boundary in this API surface, unlike anything in races.py.
    """
    response = test_client.get("/api/v1/alerts")

    assert response.status_code == 401
