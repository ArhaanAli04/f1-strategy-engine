"""Live telemetry, lap-history, and session-gap routes.

Zero business logic — see telemetry_service.py. Every route carries
@limiter.limit(rate_limit_value) — see core/rate_limit.py for why this must
be a per-route decorator rather than one global middleware default, and why
each handler below needs a `request: Request` parameter.
"""

import asyncio
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
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.exceptions import AuthenticationError, NotFoundError
from backend.core.metrics import f1_active_websocket_connections
from backend.core.rate_limit import limiter, rate_limit_value
from backend.core.redis_client import get_redis
from backend.core.security import decode_token
from backend.schemas.telemetry_schema import (
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


@router.get("/{session_id}/{driver_id}/live", response_model=LiveTelemetryResponse)
@limiter.limit(rate_limit_value)
async def get_live_telemetry(
    request: Request,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> LiveTelemetryResponse:
    return await telemetry_service.get_live_lap_for_session(redis_client, db, session_id, driver_id)


@router.get("/{session_id}/{driver_id}/history", response_model=list[LapHistoryBucket])
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


@router.get("/{session_id}/gaps", response_model=SessionGapsResponse)
@limiter.limit(rate_limit_value)
async def get_session_gaps(
    request: Request,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> SessionGapsResponse:
    return await telemetry_service.get_session_gaps_for_session(redis_client, db, session_id)


async def _forward_lap_events(
    websocket: WebSocket,
    pubsub: Any,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    session_id: uuid.UUID,
    season: int,
    round_number: int,
) -> None:
    """Forward each f1:telemetry:{session_id}:laps pub/sub message to one WS client.

    Args:
        websocket: The accepted WebSocket connection.
        pubsub: PubSub object already subscribed to this session's lap-completion channel.
        redis_client: Redis client, for the per-message live-telemetry enrichment lookup.
        session_id, season, round_number: Identifiers for the enrichment lookup key.
    Returns:
        None. Runs until cancelled — see websocket_telemetry, which races this
        against _watch_for_disconnect.
    """
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        lap_summary = json.loads(message["data"])
        channels = await telemetry_service.get_live_car_channels(
            redis_client, season, round_number, uuid.UUID(lap_summary["driver_id"])
        )
        event = LapCompletedEvent(**lap_summary, **channels)
        envelope = TelemetryStreamMessage(event="lap_completed", session_id=session_id, data=event)
        await websocket.send_text(envelope.model_dump_json())


async def _watch_for_disconnect(websocket: WebSocket) -> None:
    """Block until the client disconnects — this stream is server-push only.

    Without a concurrent receive(), Starlette never observes a client-initiated
    disconnect until the next outbound send() fails, which could be a full lap
    (tens of seconds) away. Racing this against _forward_lap_events bounds
    disconnect detection to roughly immediate instead.

    Args:
        websocket: The accepted WebSocket connection.
    Returns:
        None. Raises WebSocketDisconnect when the client disconnects.
    """
    while True:
        await websocket.receive_text()


@ws_router.websocket("/ws/telemetry/{session_id}")
async def websocket_telemetry(
    websocket: WebSocket,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
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
        db: Async DB session, to resolve season/round for the enrichment lookup.
        redis_client: Redis client, for both the pub/sub subscription and the
            per-message live-telemetry enrichment lookup.
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
        season, round_number = await telemetry_service.resolve_season_round(db, session_id)
    except NotFoundError:
        await websocket.close(code=_WS_POLICY_VIOLATION_CLOSE_CODE)
        return

    await websocket.accept()
    f1_active_websocket_connections.inc()

    channel = f"f1:telemetry:{session_id}:laps"
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel)

    forward_task = asyncio.create_task(
        _forward_lap_events(websocket, pubsub, redis_client, session_id, season, round_number)
    )
    watch_task = asyncio.create_task(_watch_for_disconnect(websocket))
    try:
        await asyncio.wait({forward_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        forward_task.cancel()
        watch_task.cancel()
        for task in (forward_task, watch_task):
            try:
                await task
            except (asyncio.CancelledError, WebSocketDisconnect):
                pass
            except Exception:
                logger.exception(
                    "Unexpected error in WS telemetry stream for session %s", session_id
                )
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()  # type: ignore[attr-defined]
        f1_active_websocket_connections.dec()
