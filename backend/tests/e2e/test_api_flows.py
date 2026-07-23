"""End-to-end API-flow test: full user journey via real HTTP against a live server.

No browser here — see test_live_race_flow.py for the (currently-skipped)
Playwright stubs. This exercises register -> login -> fetch strategy ->
simulate -> check alerts against whatever docker-compose stack is already
running (see conftest.py's base_url/authenticated_session/session_id
fixtures), the same infrastructure the Day 18 load tests ran against.
"""

from __future__ import annotations

import time

import pytest
import requests

_SIMULATE_POLL_TIMEOUT_SECONDS = 120.0
_SIMULATE_POLL_INTERVAL_SECONDS = 1.0
_TERMINAL_TASK_STATUSES = frozenset({"SUCCESS", "FAILURE"})


@pytest.mark.e2e
def test_full_user_journey(
    base_url: str, authenticated_session: requests.Session, session_id: str
) -> None:
    """register -> login (via fixtures) -> fetch strategy -> simulate -> check alerts."""
    me_response = authenticated_session.get(f"{base_url}/api/v1/auth/me", timeout=15)
    assert me_response.status_code == 200

    # /strategy/overview's cold-compute path can take 16-17s (see CLAUDE.md's
    # "get_competitor_predicted_strategy 16-17s cold compute floor" note).
    overview_response = authenticated_session.get(
        f"{base_url}/api/v1/strategy/{session_id}/overview", timeout=60
    )
    assert overview_response.status_code == 200
    drivers = overview_response.json()["drivers"]
    assert drivers, "expected at least one driver in the strategy overview"
    driver_id = drivers[0]["driver_id"]

    simulate_response = authenticated_session.post(
        f"{base_url}/api/v1/strategy/{session_id}/simulate",
        json={
            "driver_id": driver_id,
            "current_lap": 20,
            "current_compound": "MEDIUM",
            "current_tyre_age": 5,
            "remaining_laps": 37,
            "pit_laps": [],
            "compounds": [],
        },
        timeout=15,
    )
    assert simulate_response.status_code == 202
    task_id = simulate_response.json()["task_id"]

    status = "PENDING"
    deadline = time.monotonic() + _SIMULATE_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        poll_response = authenticated_session.get(
            f"{base_url}/api/v1/strategy/simulate/{task_id}", timeout=15
        )
        assert poll_response.status_code == 200
        status = poll_response.json()["status"]
        if status in _TERMINAL_TASK_STATUSES:
            break
        time.sleep(_SIMULATE_POLL_INTERVAL_SECONDS)
    assert status == "SUCCESS", f"simulate task did not succeed in time (last status: {status})"

    alerts_response = authenticated_session.get(f"{base_url}/api/v1/alerts", timeout=15)
    assert alerts_response.status_code == 200
    assert isinstance(alerts_response.json(), list)
