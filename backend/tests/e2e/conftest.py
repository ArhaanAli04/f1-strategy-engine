"""E2E fixtures: register/login and discover real data against a live running server.

Unlike backend/tests/integration/conftest.py's testcontainers-based fixtures
(fresh Postgres + Redis spun up per test session, schema created from
scratch), these E2E tests exercise the actual docker-compose stack a
developer already has running locally (see CLAUDE.md's Day 18 notes and
docs/load_test_results.md) — there is no schema-creation step here, and no
in-process TestClient/app object to attach to, since these go over real
HTTP against whatever host is already serving traffic.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

import pytest
import requests

_DEFAULT_BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("E2E_BASE_URL", _DEFAULT_BASE_URL)


@pytest.fixture
def authenticated_session(base_url: str) -> Generator[requests.Session, None, None]:
    """A requests.Session with a real access token, from a throwaway registered user.

    Mirrors integration/conftest.py's authenticated_client fixture (register
    -> login through the real HTTP auth flow, not a service-layer shortcut),
    but returns a plain requests.Session against the live host instead of a
    FastAPI TestClient — there is no in-process app object here to wrap.
    """
    email = f"e2e-{uuid.uuid4()}@example.com"
    password = "E2ETest123!"  # noqa: S105 — throwaway local test account, not a secret
    session = requests.Session()

    register_resp = session.post(
        f"{base_url}/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "E2E Test User"},
        timeout=15,
    )
    register_resp.raise_for_status()

    login_resp = session.post(
        f"{base_url}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=15,
    )
    login_resp.raise_for_status()
    access_token: str = login_resp.json()["access_token"]
    session.headers.update({"Authorization": f"Bearer {access_token}"})

    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def session_id(base_url: str) -> str:
    """A real session_id with ingested lap data, discovered via the live API.

    Not hardcoded: walks GET /races -> GET /races/{id} looking for a race
    with at least one session, preferring session_type == "R" (a full race
    session has the richest data for exercising /strategy endpoints).
    Skips the test if this environment has no ingested race data yet, rather
    than failing — that's a legitimate environment state (e.g. a fresh
    docker compose up with no ingestion run yet), not a test bug.
    """
    races_resp = requests.get(
        f"{base_url}/api/v1/races", params={"page": 1, "page_size": 20}, timeout=15
    )
    races_resp.raise_for_status()
    races: list[dict[str, object]] = races_resp.json()["items"]
    if not races:
        pytest.skip("No ingested races found — run backend/scripts/ingest_historical.py first")

    for race in races:
        race_resp = requests.get(f"{base_url}/api/v1/races/{race['id']}", timeout=15)
        race_resp.raise_for_status()
        sessions: list[dict[str, object]] = race_resp.json()["sessions"]
        if not sessions:
            continue
        race_session = next((s for s in sessions if s["session_type"] == "R"), sessions[0])
        return str(race_session["id"])

    pytest.skip("No race with an ingested session found in this environment")
    raise AssertionError("unreachable")  # pytest.skip always raises; satisfies mypy's return check
