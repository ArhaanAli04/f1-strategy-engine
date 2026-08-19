"""Live telemetry, lap-history, and session-gap routes.

Zero business logic — see telemetry_service.py. Every route carries
@limiter.limit(rate_limit_value) — see core/rate_limit.py for why this must
be a per-route decorator rather than one global middleware default, and why
each handler below needs a `request: Request` parameter.
"""

import asyncio
import functools
import json
import logging
import uuid
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_db, get_engine
from backend.core.exceptions import AuthenticationError, NotFoundError
from backend.core.metrics import f1_active_websocket_connections
from backend.core.rate_limit import limiter, rate_limit_value
from backend.core.redis_client import get_redis
from backend.core.security import decode_token
from backend.schemas.telemetry_schema import (
    DriverCarNumber,
    DriverPosition,
    LapCompletedEvent,
    LapHistoryBucket,
    LiveTelemetryResponse,
    SessionGapsResponse,
    TelemetryStreamMessage,
)
from backend.services import telemetry_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])
ws_router = APIRouter()

# Application-defined WS close code (4000-4999 range reserved for this) for
# any auth/lookup failure during the handshake — matches the codebase's
# HTTP-side convention of a single AuthenticationError-shaped rejection.
_WS_POLICY_VIOLATION_CLOSE_CODE = 4401

# A dedicated short-lived-session factory (same pattern as workers/*.py),
# rather than Depends(get_db): that FastAPI dependency stays open for the
# entire route call, which for a WebSocket route means the whole connection's
# lifetime. The one-time season/round lookup below doesn't need a DB
# connection pinned for as long as a viewer stays connected — with
# pool_size=10 + max_overflow=20 (see core/database.py), holding one per
# connection capped concurrent WS viewers at ~30, confirmed via
# tests/load/ws_load_test.py.
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


@router.get(
    "/{session_id}/{driver_id}/live",
    response_model=LiveTelemetryResponse,
    summary="Get a driver's latest live car telemetry sample",
    description=(
        "Returns the most recent decoded CarData sample (speed, gear, "
        "throttle, brake, DRS status) for one driver from the live Redis "
        "cache — empty/None fields if no live ingestor is currently running "
        "for this session."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": {
                            "session_id": "3f9c1e2a-7b4d-4e6a-8c1f-2a5d9b8e4c10",
                            "driver_id": "8e2f9c1a-3b7d-4e2a-9f1c-6a5d2b8e4f10",
                            "data": {
                                "speed_kmh": 312.0,
                                "gear": 7,
                                "throttle_pct": 100.0,
                                "brake": False,
                                "drs": "open",
                            },
                        }
                    }
                }
            }
        }
    },
)
@limiter.limit(rate_limit_value)
async def get_live_telemetry(
    request: Request,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> LiveTelemetryResponse:
    return await telemetry_service.get_live_lap_for_session(redis_client, db, session_id, driver_id)


@router.get(
    "/{session_id}/{driver_id}/history",
    response_model=list[LapHistoryBucket],
    summary="Get a driver's recent lap-time history buckets",
    description=(
        "Returns average sector/lap times over the last N laps for one "
        "driver — used for pace-trend charts."
    ),
)
@limiter.limit(rate_limit_value)
async def get_driver_lap_history(
    request: Request,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    laps: int = Query(5, ge=1, le=100),
) -> list[LapHistoryBucket]:
    return await telemetry_service.get_lap_history_for_session(
        redis_client, db, session_id, driver_id, laps
    )


@router.get(
    "/{session_id}/gaps",
    response_model=SessionGapsResponse,
    summary="Get gap-to-ahead/behind for every driver in a session",
    description=(
        "Returns each driver's current position and gap (seconds) to the "
        "car ahead and behind — feeds the timing tower."
    ),
)
@limiter.limit(rate_limit_value)
async def get_session_gaps(
    request: Request,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> SessionGapsResponse:
    return await telemetry_service.get_session_gaps_for_session(redis_client, db, session_id)


@router.get(
    "/{session_id}/positions",
    response_model=list[DriverPosition],
    summary="Get every car's latest live X/Y position",
    description=(
        "Returns each car's latest live track position (keyed by car "
        "number, not driver_id) for the Circuit Map Panel's moving dots."
    ),
)
@limiter.limit(rate_limit_value)
async def get_session_positions(
    request: Request,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> list[DriverPosition]:
    return await telemetry_service.get_driver_positions_for_session(redis_client, db, session_id)


@router.get(
    "/{session_id}/car-numbers",
    response_model=list[DriverCarNumber],
    summary="Get the driver_id-to-car-number mapping for a session",
    description=(
        "Bridges live position data (keyed by car number) to the driver "
        "roster (keyed by driver_id) — self-corrects for reserve-driver "
        "substitutions."
    ),
)
@limiter.limit(rate_limit_value)
async def get_session_car_numbers(
    request: Request,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> list[DriverCarNumber]:
    return await telemetry_service.get_driver_car_numbers_for_session(redis_client, db, session_id)


class _SessionBroadcaster:
    """Fans out one session's lap-completion events to every connected WS client.

    One instance lives per active session_id — created on the first
    subscriber and torn down on the last disconnect (see _acquire_broadcaster
    / _release_broadcaster) — replacing the old one-pubsub-per-connection
    model where every connected viewer independently repeated the same
    get_live_car_channels Redis lookup on every event (see CLAUDE.md's WS
    fan-out deferred-wiring entry).

    Owns its own Redis client, built from the connecting request's
    connection_pool rather than reusing that request's own DI-scoped client:
    the request that happens to create this broadcaster can disconnect long
    before the broadcaster itself is torn down (other viewers may still be
    connected), and FastAPI's get_redis() dependency would already have
    aclose()'d that client at that point.
    """

    def __init__(
        self,
        session_id: uuid.UUID,
        season: int,
        round_number: int,
        redis_client: aioredis.Redis,  # type: ignore[type-arg]
    ) -> None:
        self.session_id = session_id
        self.season = season
        self.round_number = round_number
        self.channel = f"f1:telemetry:{session_id}:laps"
        self.connections: set[WebSocket] = set()
        self._redis: aioredis.Redis = aioredis.Redis(  # type: ignore[type-arg]
            connection_pool=redis_client.connection_pool
        )
        self._pubsub: Any = self._redis.pubsub()
        self._listen_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Subscribe to this session's channel and start the shared listen loop."""
        await self._pubsub.subscribe(self.channel)
        self._listen_task = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        async for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            lap_summary = json.loads(message["data"])
            channels = await telemetry_service.get_live_car_channels(
                self._redis, self.season, self.round_number, uuid.UUID(lap_summary["driver_id"])
            )
            event = LapCompletedEvent(**lap_summary, **channels)
            envelope = TelemetryStreamMessage(
                event="lap_completed", session_id=self.session_id, data=event
            )
            text = envelope.model_dump_json()
            for websocket in list(self.connections):
                try:
                    await websocket.send_text(text)
                except Exception:
                    logger.warning(
                        "Failed to deliver lap event to one WS client in session %s "
                        "(its own disconnect-detection will clean it up)",
                        self.session_id,
                    )

    async def stop(self) -> None:
        """Cancel the listen loop, unsubscribe, and release this broadcaster's Redis client."""
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "Unexpected error shutting down WS broadcaster for session %s",
                    self.session_id,
                )
        await self._pubsub.unsubscribe(self.channel)
        # redis-py 6.4.0's PubSub.aclose() can hang indefinitely on
        # connection.disconnect() when the listen loop was cancelled mid-read
        # on this same connection — confirmed via a manual print-trace during
        # Day 17 test-writing: unsubscribe completes, but aclose() never
        # returns. Worse, even asyncio.wait_for(aclose(), timeout=...) doesn't
        # rescue it: wait_for cancels the inner task and then awaits its
        # actual completion, which never comes either, since the underlying
        # connection is stuck in a non-cancellable state (not an ordinary
        # "slow" case a timeout can bound). Running it as a detached
        # background task instead means a wedged connection can never block
        # this method's return — worst case it abandons that one broadcaster's
        # connection rather than stalling every teardown.
        cleanup_task = asyncio.create_task(self._pubsub.aclose())
        cleanup_task.add_done_callback(
            functools.partial(_log_pubsub_cleanup_failure, self.session_id)
        )
        await self._redis.aclose()  # type: ignore[attr-defined]


_broadcasters: dict[uuid.UUID, _SessionBroadcaster] = {}
_registry_lock = asyncio.Lock()


async def _acquire_broadcaster(
    session_id: uuid.UUID,
    season: int,
    round_number: int,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    websocket: WebSocket,
) -> _SessionBroadcaster:
    """Get-or-create the shared broadcaster for session_id and register websocket on it.

    Args:
        session_id, season, round_number: Identifiers for a new broadcaster —
            ignored if one already exists for this session_id.
        redis_client: The connecting request's DI-scoped Redis client — only
            used for its connection_pool if a new broadcaster must be created.
        websocket: The just-accepted connection to add as a fan-out target.
    Returns:
        The (possibly newly created) broadcaster for this session_id.
    """
    async with _registry_lock:
        broadcaster = _broadcasters.get(session_id)
        if broadcaster is None:
            broadcaster = _SessionBroadcaster(session_id, season, round_number, redis_client)
            await broadcaster.start()
            _broadcasters[session_id] = broadcaster
        broadcaster.connections.add(websocket)
        return broadcaster


async def _release_broadcaster(session_id: uuid.UUID, websocket: WebSocket) -> None:
    """Unregister websocket from its session's broadcaster; tear down if it was the last one.

    Args:
        session_id: Session the disconnecting websocket belonged to.
        websocket: The disconnecting connection.
    Returns:
        None.
    """
    async with _registry_lock:
        broadcaster = _broadcasters.get(session_id)
        if broadcaster is None:
            return
        broadcaster.connections.discard(websocket)
        if not broadcaster.connections:
            del _broadcasters[session_id]
            await broadcaster.stop()


async def _watch_for_disconnect(websocket: WebSocket) -> None:
    """Block until the client disconnects — this stream is server-push only.

    Without a concurrent receive(), Starlette never observes a client-initiated
    disconnect until the next outbound send() fails, which could be a full lap
    (tens of seconds) away. Awaiting this directly in websocket_telemetry bounds
    disconnect detection to roughly immediate instead.

    Args:
        websocket: The accepted WebSocket connection.
    Returns:
        None. Raises WebSocketDisconnect when the client disconnects.
    """
    while True:
        await websocket.receive_text()


def _log_pubsub_cleanup_failure(session_id: uuid.UUID, task: "asyncio.Task[None]") -> None:
    """Done-callback for the detached pubsub.aclose() cleanup task.

    Args:
        session_id: The WS session the pubsub connection belonged to (for the log line).
        task: The completed (or cancelled) aclose() task.
    Returns:
        None.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("pubsub.aclose() failed in background for session %s: %s", session_id, exc)


@ws_router.websocket("/ws/telemetry/{session_id}")
async def websocket_telemetry(
    websocket: WebSocket,
    session_id: uuid.UUID,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> None:
    """Broadcast LapCompletedEvent to every client connected for a session.

    Authenticates via ?token=... (a JWT access token) rather than an
    Authorization header, since browsers' WebSocket API cannot set custom
    headers on the handshake request. This means the token appears in server
    access logs — a known, accepted limitation for now (a short-lived WS
    ticket pattern is deferred; see CLAUDE.md).

    Args:
        websocket: The incoming WebSocket connection (not yet accepted).
        session_id: Session to stream lap-completion events for.
        redis_client: This connection's DI-scoped Redis client — only used to
            seed a new _SessionBroadcaster's connection pool if this is the
            first viewer for session_id; the broadcaster then owns its own
            client for the shared subscription and enrichment lookups.
    Returns:
        None.
    """
    token = websocket.query_params.get("token")
    if token is None:
        await websocket.close(code=_WS_POLICY_VIOLATION_CLOSE_CODE)
        return

    try:
        payload = decode_token(token)
    except AuthenticationError:
        await websocket.close(code=_WS_POLICY_VIOLATION_CLOSE_CODE)
        return
    if payload.get("type") != "access":
        await websocket.close(code=_WS_POLICY_VIOLATION_CLOSE_CODE)
        return

    try:
        async with _get_session_factory()() as db:
            season, round_number = await telemetry_service.resolve_season_round(db, session_id)
    except NotFoundError:
        await websocket.close(code=_WS_POLICY_VIOLATION_CLOSE_CODE)
        return

    await websocket.accept()
    f1_active_websocket_connections.inc()

    await _acquire_broadcaster(session_id, season, round_number, redis_client, websocket)
    try:
        await _watch_for_disconnect(websocket)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Unexpected error in WS telemetry stream for session %s", session_id)
    finally:
        await _release_broadcaster(session_id, websocket)
        f1_active_websocket_connections.dec()
