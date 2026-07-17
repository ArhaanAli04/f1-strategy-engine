"""Locust load test: race-day viewer, heavy-compute strategy user, WS telemetry viewer.

Auth strategy — pre-login before the timed window, not live login per user:
core/rate_limit.py buckets every unauthenticated request (which includes
/auth/login itself — no token exists yet to bucket by) under one shared
"ip:{client_ip}" key at 10/minute. Registering+logging in N simulated users
live inside Locust's on_start would throttle ramp-up to ~10 logins/minute
from this one test-runner IP and spend most of a short baseline run just
failing logins, not exercising the endpoints under test. Instead,
@events.test_start below provisions/logs in a small pool of test accounts
*before* Locust starts spawning users and measuring anything, matching how a
real client actually behaves (log in once, reuse the token) — each
simulated User then just reads a pre-fetched access token in on_start.

Account pool sizing — shared, not 1:1 with simulated users: at 100 concurrent
users, provisioning 100 real accounts would itself need ~200 unauthenticated
requests (register + login each) against the same 10/minute ceiling — about
20 minutes of one-time setup. LOAD_TEST_USER_POOL_SIZE (default: min(target
users, 30)) trades that off against realism: each account then backs a few
concurrent simulated users, round-robin. At the request rates below (~12/min
for RaceDayViewerUser, ~2/min for StrategyUser), a handful of simulated users
sharing one account still stays comfortably under the 60/min authenticated
bucket. Tokens are cached to backend/tests/load/.token_cache.json (gitignored
— see .gitignore) so a same-day re-run (e.g. baseline, then again after DB
indexes land) reuses valid tokens instantly instead of re-paying setup cost.

Required env var:
    LOAD_TEST_SESSION_ID     Real session UUID with ingested lap_data.
Required for StrategyUser:
    LOAD_TEST_DRIVER_IDS     Comma-separated real driver UUIDs from that session.
Optional:
    LOAD_TEST_USER_POOL_SIZE       Override the account pool size (see above).
    LOAD_TEST_CURRENT_LAP           Default 20 — a real observed lap number.
    LOAD_TEST_CURRENT_COMPOUND      Default INTERMEDIATE.
    LOAD_TEST_CURRENT_TYRE_AGE      Default 20.
    LOAD_TEST_REMAINING_LAPS        Default 37 (57-lap session, real total_laps).

Run (baseline, per CLAUDE.md Day 13 spec):
    locust -f backend/tests/load/locustfile.py --headless -u 100 -r 10 \
        --run-time 2m --host http://localhost:8000

Run replay_publisher.py in a second terminal for WebSocketUser to see real
message traffic (see that script's docstring) — without it, WS connections
stay open but receive nothing to measure.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from locust import HttpUser, User, between, events, task
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection
from websockets.sync.client import connect as ws_connect

logger = logging.getLogger(__name__)

_TOKEN_CACHE_PATH = Path(__file__).parent / ".token_cache.json"
# core/rate_limit.py's UNAUTHENTICATED_LIMIT = "10/minute" — every register/
# login call from this un-authenticated test-runner IP shares that bucket.
_UNAUTHENTICATED_CALLS_PER_MINUTE = 10
_SECONDS_BETWEEN_UNAUTH_CALLS = 60.0 / _UNAUTHENTICATED_CALLS_PER_MINUTE
_DEFAULT_POOL_SIZE_CAP = 30
# Re-use a cached token if it has at least this long left before expiry —
# avoids a token expiring mid-request near the boundary.
_TOKEN_REUSE_SAFETY_MARGIN_SECONDS = 120

# .local/.test/.invalid are IANA special-use TLDs that pydantic's EmailStr
# (via email-validator) rejects outright at the syntax level — example.com is
# a real public TLD, so it passes validation despite being RFC 2606-reserved.
_TEST_USER_EMAIL_DOMAIN = "example.com"
_TEST_USER_PASSWORD = "LoadTest123!"  # noqa: S105 — throwaway local test account, not a secret

_WS_RECV_TIMEOUT_SECONDS = 10.0

_token_pool: list[str] = []
_token_cycle: itertools.cycle[str] | None = None
_driver_id_cycle: itertools.cycle[str] | None = None


def _env_or_raise(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} env var is required — see locustfile.py's module docstring")
    return value


def _session_id() -> str:
    return _env_or_raise("LOAD_TEST_SESSION_ID")


def _driver_ids() -> list[str]:
    return [d.strip() for d in _env_or_raise("LOAD_TEST_DRIVER_IDS").split(",") if d.strip()]


def _next_token() -> str:
    assert _token_cycle is not None, "test_start pre-login hook did not run"  # noqa: S101
    return next(_token_cycle)


def _next_driver_id() -> str:
    global _driver_id_cycle
    if _driver_id_cycle is None:
        _driver_id_cycle = itertools.cycle(_driver_ids())
    return next(_driver_id_cycle)


def _load_token_cache() -> dict[str, dict[str, str]]:
    if not _TOKEN_CACHE_PATH.exists():
        return {}
    try:
        data: dict[str, dict[str, str]] = json.loads(_TOKEN_CACHE_PATH.read_text())
        return data
    except (json.JSONDecodeError, OSError):
        return {}


def _save_token_cache(cache: dict[str, dict[str, str]]) -> None:
    _TOKEN_CACHE_PATH.write_text(json.dumps(cache, indent=2))


def _token_still_valid(entry: dict[str, str]) -> bool:
    expires_at = datetime.fromisoformat(entry["expires_at"])
    return expires_at - timedelta(seconds=_TOKEN_REUSE_SAFETY_MARGIN_SECONDS) > datetime.now(UTC)


def _register_and_login(host: str, email: str, password: str) -> dict[str, str]:
    """Register (tolerating an already-registered account) then log in.

    Args:
        host: Base URL, e.g. http://localhost:8000.
        email: Test account email.
        password: Test account password.
    Returns:
        {"access_token": ..., "expires_at": ISO-8601 string}.
    """
    register_resp = requests.post(
        f"{host}/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Load Test User"},
        timeout=10,
    )
    if register_resp.status_code not in (201, 409):
        register_resp.raise_for_status()
    time.sleep(_SECONDS_BETWEEN_UNAUTH_CALLS)

    login_resp = requests.post(
        f"{host}/api/v1/auth/login", json={"email": email, "password": password}, timeout=10
    )
    login_resp.raise_for_status()
    body = login_resp.json()
    return {"access_token": body["access_token"], "expires_at": body["expires_at"]}


def _target_pool_size(environment: Any) -> int:
    override = os.environ.get("LOAD_TEST_USER_POOL_SIZE")
    if override:
        return int(override)
    num_users = getattr(environment.parsed_options, "num_users", None) or 100
    return min(int(num_users), _DEFAULT_POOL_SIZE_CAP)


@events.test_start.add_listener  # type: ignore[untyped-decorator]
def _provision_test_users(environment: Any, **kwargs: Any) -> None:
    """Pre-login a pool of test accounts before Locust starts spawning users.

    See module docstring for the rate-limit rationale.
    """
    host = environment.host or "http://localhost:8000"
    pool_size = _target_pool_size(environment)
    cache = _load_token_cache()

    new_logins_needed = sum(
        1
        for i in range(pool_size)
        if f"loadtest{i}@{_TEST_USER_EMAIL_DOMAIN}" not in cache
        or not _token_still_valid(cache[f"loadtest{i}@{_TEST_USER_EMAIL_DOMAIN}"])
    )
    if new_logins_needed:
        logger.info(
            "Provisioning %d/%d test account(s) (%.0fs each, rate-limit-paced) — this only "
            "happens for accounts whose cached token has expired or never existed",
            new_logins_needed,
            pool_size,
            _SECONDS_BETWEEN_UNAUTH_CALLS * 2,
        )

    for i in range(pool_size):
        email = f"loadtest{i}@{_TEST_USER_EMAIL_DOMAIN}"
        cached = cache.get(email)
        if cached is not None and _token_still_valid(cached):
            _token_pool.append(cached["access_token"])
            continue

        try:
            entry = _register_and_login(host, email, _TEST_USER_PASSWORD)
        except requests.RequestException as exc:
            # Locust fires test_start listeners without blocking user spawn on
            # a raised exception — it just logs "Uncaught exception in event
            # handler" and carries on (verified against locust 2.44.4). A
            # per-account failure must not silently propagate and skip the
            # not-empty check below, so it's caught and logged here instead.
            logger.warning("Failed to provision test account %s: %s", email, exc)
            continue
        cache[email] = entry
        _token_pool.append(entry["access_token"])
        # Saved after every account, not once at the end of the loop: Locust's
        # --run-time timer runs concurrently with this (synchronous,
        # rate-limit-paced) provisioning pass — see module docstring — and can
        # cut it short before the loop finishes. Saving only at the end meant
        # a run-time cutoff mid-provisioning silently discarded every account
        # already paid for in this pass, forcing a full re-provision from
        # scratch on the next attempt instead of resuming.
        _save_token_cache(cache)
        logger.info("Provisioned test account %d/%d", i + 1, pool_size)

    if not _token_pool:
        logger.error("No test accounts provisioned — aborting load test")
        if environment.runner is not None:
            environment.runner.quit()
        sys.exit(1)

    global _token_cycle
    _token_cycle = itertools.cycle(_token_pool)
    logger.info("Test account pool ready: %d account(s)", len(_token_pool))


class RaceDayViewerUser(HttpUser):
    """The spec's "User": hits /races/current, then /strategy/{session_id}/overview every ~5s."""

    wait_time = between(4, 6)

    def on_start(self) -> None:
        self.client.headers["Authorization"] = f"Bearer {_next_token()}"
        self.session_id = _session_id()
        # /races/current resolves via Ergast against *today's* real calendar
        # date — against historical 2018-2025 ingested data this legitimately
        # 404s outside race week. That's still real endpoint behavior worth
        # measuring (external Ergast API latency, DB round trip), so both
        # 200 and 404 count as a successful exercise of the route; anything
        # else is a real failure.
        with self.client.get(
            "/api/v1/races/current", name="/races/current", catch_response=True
        ) as resp:
            if resp.status_code in (200, 404):
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}")

    @task
    def strategy_overview(self) -> None:
        self.client.get(
            f"/api/v1/strategy/{self.session_id}/overview", name="/strategy/[session_id]/overview"
        )


class StrategyUser(HttpUser):
    """Heavy-compute user: POSTs /strategy/{session_id}/simulate every ~30s."""

    wait_time = between(28, 32)

    def on_start(self) -> None:
        self.client.headers["Authorization"] = f"Bearer {_next_token()}"
        self.session_id = _session_id()
        self.driver_id = _next_driver_id()
        self.current_lap = int(os.environ.get("LOAD_TEST_CURRENT_LAP", "20"))
        self.current_compound = os.environ.get("LOAD_TEST_CURRENT_COMPOUND", "INTERMEDIATE")
        self.current_tyre_age = int(os.environ.get("LOAD_TEST_CURRENT_TYRE_AGE", "20"))
        self.remaining_laps = int(os.environ.get("LOAD_TEST_REMAINING_LAPS", "37"))

    @task
    def simulate(self) -> None:
        payload = {
            "driver_id": self.driver_id,
            "current_lap": self.current_lap,
            "current_compound": self.current_compound,
            "current_tyre_age": self.current_tyre_age,
            "remaining_laps": self.remaining_laps,
            "pit_laps": [],
            "compounds": [],
        }
        self.client.post(
            f"/api/v1/strategy/{self.session_id}/simulate",
            json=payload,
            name="/strategy/[session_id]/simulate",
        )


def _to_ws_url(host: str, session_id: str, token: str) -> str:
    ws_scheme_host = host.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_scheme_host}/api/v1/ws/telemetry/{session_id}?token={token}"


class WebSocketUser(User):
    """Connects to /ws/telemetry/{session_id}, stays connected, measures per-message latency.

    Requires backend/tests/load/replay_publisher.py running alongside this
    Locust run — see module docstring — otherwise the connection stays open
    but idle (recv() just times out every _WS_RECV_TIMEOUT_SECONDS, which is
    treated as "no event this interval," not a failure).
    """

    wait_time = between(1, 2)
    _ws: ClientConnection

    def on_start(self) -> None:
        session_id = _session_id()
        token = _next_token()
        ws_url = _to_ws_url(self.host or "http://localhost:8000", session_id, token)
        self._ws = ws_connect(ws_url, open_timeout=10)

    @task
    def listen(self) -> None:
        start = time.monotonic()
        try:
            message = self._ws.recv(timeout=_WS_RECV_TIMEOUT_SECONDS)
        except TimeoutError:
            return
        except ConnectionClosed as exc:
            events.request.fire(  # type: ignore[no-untyped-call]
                request_type="WS",
                name="lap_completed",
                response_time=0,
                response_length=0,
                exception=exc,
            )
            return
        elapsed_ms = (time.monotonic() - start) * 1000
        events.request.fire(  # type: ignore[no-untyped-call]
            request_type="WS",
            name="lap_completed",
            response_time=elapsed_ms,
            response_length=len(message),
            exception=None,
        )

    def on_stop(self) -> None:
        # on_start's ws_connect() can raise (e.g. handshake timeout) before
        # ever assigning self._ws — Locust still calls on_stop for a user
        # whose on_start failed, so this must not assume the connection
        # exists.
        if hasattr(self, "_ws"):
            self._ws.close()
