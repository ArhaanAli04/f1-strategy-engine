"""Self-contained asyncio WS load test: direct connections, publisher-owned latency.

Decoupled from replay_publisher.py (which feeds locustfile.py's WebSocketUser
by replaying real historical laps through the normal ingestion-shaped Redis
message). This script is a standalone benchmark with no dependency on Locust
or the database: it logs in once, opens N WebSocket connections directly to
/ws/telemetry/{session_id}, then injects its own synthetic
LapCompletedEvent-shaped messages onto the same f1:telemetry:{session_id}:laps
channel _forward_lap_events (backend/apis/v1/telemetry.py) subscribes to, and
measures publish -> receipt latency across all N clients at once.

Latency measurement trick: publisher and clients run as asyncio tasks in this
one process, sharing one clock. Rather than threading a timestamp through a
side-channel dict, the publish time is smuggled through LapCompletedEvent's
own `lap_time_seconds` field (a plain float with no range validation) and
`lap_number` carries a sequence id — so latency and loss can both be computed
from fields the schema already round-trips, with zero server-side changes.

All N connections share one JWT: unlike locustfile.py's account pool (sized
to respect core/rate_limit.py's 10/minute unauthenticated bucket across many
concurrent *simulated users* making independent business requests), this
script only needs one successful login for a purely server-push read path —
the token is never re-sent after the WS handshake.

Run:
    python backend/tests/load/ws_load_test.py --session-id <uuid> \\
        --connections 200 --messages 50 --rate 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import time
import uuid
from dataclasses import dataclass, field

import redis.asyncio as aioredis
import requests
import websockets
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect

from backend.core.config import get_redis_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_CONNECTIONS = 200
DEFAULT_MESSAGES = 50
DEFAULT_RATE_PER_SECOND = 20.0

_WS_CONNECT_TIMEOUT_SECONDS = 10.0
_RECV_TIMEOUT_SECONDS = 5.0
# Grace period after the last publish before clients stop listening, so
# in-flight messages (Redis pub/sub + WS send latency) aren't cut off.
_DRAIN_SECONDS = 3.0

_TEST_USER_EMAIL = "ws_load_test@example.com"
_TEST_USER_PASSWORD = "LoadTest123!"  # noqa: S105 — throwaway local test account, not a secret


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Direct asyncio WS load test against /ws/telemetry/{session_id}."
    )
    parser.add_argument("--session-id", type=uuid.UUID, required=True)
    parser.add_argument("--host", default="http://localhost:8000")
    parser.add_argument("--connections", type=int, default=DEFAULT_CONNECTIONS)
    parser.add_argument("--messages", type=int, default=DEFAULT_MESSAGES)
    parser.add_argument(
        "--rate",
        type=float,
        default=DEFAULT_RATE_PER_SECOND,
        help="Synthetic lap-completion events published per second (default: %(default)s)",
    )
    return parser.parse_args()


def _login(host: str) -> str:
    """Register (tolerating an already-registered account) then log in once.

    Args:
        host: Base URL, e.g. http://localhost:8000.
    Returns:
        A JWT access token, shared by every simulated WS connection.
    """
    register_resp = requests.post(
        f"{host}/api/v1/auth/register",
        json={
            "email": _TEST_USER_EMAIL,
            "password": _TEST_USER_PASSWORD,
            "full_name": "WS Load Test User",
        },
        timeout=10,
    )
    if register_resp.status_code not in (201, 409):
        register_resp.raise_for_status()

    login_resp = requests.post(
        f"{host}/api/v1/auth/login",
        json={"email": _TEST_USER_EMAIL, "password": _TEST_USER_PASSWORD},
        timeout=10,
    )
    login_resp.raise_for_status()
    access_token: str = login_resp.json()["access_token"]
    return access_token


def _to_ws_url(host: str, session_id: uuid.UUID, token: str) -> str:
    ws_scheme_host = host.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_scheme_host}/api/v1/ws/telemetry/{session_id}?token={token}"


@dataclass
class ClientResult:
    """Per-connection outcome, aggregated by main() after all tasks finish."""

    latencies_ms: list[float] = field(default_factory=list)
    seen_seq_ids: set[int] = field(default_factory=set)
    connect_error: str | None = None
    connected: asyncio.Event = field(default_factory=asyncio.Event)


async def _client(ws_url: str, result: ClientResult, stop_at_holder: list[float]) -> None:
    """One simulated viewer: connect, signal readiness, then record latency per message.

    Args:
        ws_url: Full ws:// URL including the ?token= query param.
        result: This connection's result bucket, mutated in place.
        stop_at_holder: A 1-element list holding the time.monotonic() deadline
            to stop listening at. It's a list (not a plain float) because the
            real deadline isn't known until every connection has signaled
            ready and the publisher's duration is added on top — main()
            back-fills it after connect, before this task's first recv().
    Returns:
        None.
    """
    try:
        async with ws_connect(ws_url, open_timeout=_WS_CONNECT_TIMEOUT_SECONDS) as ws:
            result.connected.set()
            await _listen_until(ws, result, stop_at_holder)
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        result.connect_error = f"{type(exc).__name__}: {exc}"
        result.connected.set()  # unblock main()'s readiness wait even on failure


async def _listen_until(
    ws: ClientConnection, result: ClientResult, stop_at_holder: list[float]
) -> None:
    while True:
        remaining = stop_at_holder[0] - time.monotonic()
        if remaining <= 0:
            return
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, _RECV_TIMEOUT_SECONDS))
        except TimeoutError:
            continue
        recv_time = time.monotonic()
        envelope = json.loads(raw)
        data = envelope["data"]
        seq_id = data["lap_number"]
        if seq_id in result.seen_seq_ids:
            continue  # broadcast channel — a slow client could double count on reconnect
        result.seen_seq_ids.add(seq_id)
        publish_time = data["lap_time_seconds"]
        result.latencies_ms.append((recv_time - publish_time) * 1000)


async def _publish_synthetic_laps(
    session_id: uuid.UUID, num_messages: int, rate_per_second: float
) -> None:
    """Publish num_messages synthetic LapCompletedEvent-shaped messages at a fixed rate.

    Args:
        session_id: Target session — determines the channel name.
        num_messages: Total messages to publish.
        rate_per_second: Publish rate; interval = 1 / rate.
    Returns:
        None.
    """
    channel = f"f1:telemetry:{session_id}:laps"
    client = aioredis.Redis.from_url(get_redis_settings().redis_url, decode_responses=True)
    interval = 1.0 / rate_per_second
    driver_id = str(uuid.uuid4())  # arbitrary — telemetry_service degrades gracefully to None
    try:
        for seq_id in range(num_messages):
            payload = {
                "driver_id": driver_id,
                "session_id": str(session_id),
                "lap_number": seq_id,
                "lap_time_seconds": time.monotonic(),
                "compound": "MEDIUM",
                "sector1_seconds": 28.0,
                "sector2_seconds": 31.0,
                "sector3_seconds": 27.0,
            }
            await client.publish(channel, json.dumps(payload))
            await asyncio.sleep(interval)
    finally:
        await client.aclose()  # type: ignore[attr-defined]


def _print_report(results: list[ClientResult], num_messages: int) -> None:
    failed = [r for r in results if r.connect_error is not None]
    ok = [r for r in results if r.connect_error is None]
    all_latencies = [ms for r in ok for ms in r.latencies_ms]
    received_counts = [len(r.seen_seq_ids) for r in ok]

    print("\n=== WS Load Test Report ===")
    print(f"Connections requested:   {len(results)}")
    print(f"Connections failed:      {len(failed)}")
    if failed:
        for r in failed[:5]:
            print(f"  - {r.connect_error}")
        if len(failed) > 5:
            print(f"  ... and {len(failed) - 5} more")

    if not ok:
        print("No successful connections — nothing to report.")
        return

    print(f"Messages published:      {num_messages}")
    print(f"Avg messages/connection: {statistics.mean(received_counts):.1f} / {num_messages}")
    print(f"Min messages/connection: {min(received_counts)} / {num_messages}")

    if all_latencies:
        sorted_ms = sorted(all_latencies)
        print(f"\nLatency across {len(all_latencies)} received messages (ms):")
        print(f"  min:    {sorted_ms[0]:.1f}")
        print(f"  p50:    {statistics.median(sorted_ms):.1f}")
        print(f"  p95:    {sorted_ms[int(len(sorted_ms) * 0.95)]:.1f}")
        print(f"  p99:    {sorted_ms[int(len(sorted_ms) * 0.99)]:.1f}")
        print(f"  max:    {sorted_ms[-1]:.1f}")
    else:
        print("No messages received by any connection.")


async def main() -> None:
    args = _parse_args()

    logger.info("Logging in once for %d shared connections", args.connections)
    token = _login(args.host)
    ws_url = _to_ws_url(args.host, args.session_id, token)

    results = [ClientResult() for _ in range(args.connections)]
    stop_at_holder = [float("inf")]  # real deadline filled in once every connection is ready

    logger.info("Opening %d WS connections to %s", args.connections, ws_url.split("?token=")[0])
    client_tasks = [
        asyncio.create_task(_client(ws_url, result, stop_at_holder)) for result in results
    ]
    await asyncio.wait_for(
        asyncio.gather(*(r.connected.wait() for r in results)),
        timeout=_WS_CONNECT_TIMEOUT_SECONDS + 5.0,
    )
    connected_count = sum(1 for r in results if r.connect_error is None)
    logger.info("%d/%d connections ready", connected_count, args.connections)
    # Small safety margin: the server subscribes to the Redis channel *after*
    # accepting the WS handshake (see websocket_telemetry), so a client-side
    # "connected" signal is a few microseconds ahead of the server actually
    # being subscribed. Cheap insurance against that race, not a rate limit.
    await asyncio.sleep(0.2)

    publish_duration = args.messages / args.rate
    stop_at_holder[0] = time.monotonic() + publish_duration + _DRAIN_SECONDS

    logger.info("Publishing %d synthetic lap events at %.1f/sec", args.messages, args.rate)
    await _publish_synthetic_laps(args.session_id, args.messages, args.rate)

    await asyncio.gather(*client_tasks)
    _print_report(results, args.messages)


if __name__ == "__main__":
    asyncio.run(main())
