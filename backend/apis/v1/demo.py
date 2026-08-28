"""Demo Replay control routes. Zero business logic — see services/demo_service.py.

GET routes (sessions / replay availability / replay status) are public, same
as the /races routes — they only read state. POST start/stop require
Depends(get_current_user): they launch and kill a subprocess, so they are
gated the same way the compute-heavy /strategy routes are.

Every route carries @limiter.limit(rate_limit_value) and a `request: Request`
parameter — see core/rate_limit.py.
"""

from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, status

from backend.core.rate_limit import limiter, rate_limit_value
from backend.core.redis_client import get_redis
from backend.core.security import get_current_user
from backend.schemas.demo_schema import (
    CuratedSessionsResponse,
    ReplayAvailableResponse,
    ReplayStartRequest,
    ReplayStartResponse,
    ReplayStatusResponse,
    ReplayStopResponse,
)
from backend.services import demo_service

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get(
    "/sessions",
    response_model=CuratedSessionsResponse,
    summary="List the curated Demo Replay sessions",
    description=(
        "Returns the three fixed curated sessions with race name, circuit, "
        "lap window, and an estimated duration."
    ),
)
@limiter.limit(rate_limit_value)
async def list_demo_sessions(request: Request) -> CuratedSessionsResponse:
    return demo_service.list_curated_sessions()


@router.get(
    "/replay/available",
    response_model=ReplayAvailableResponse,
    summary="Whether a Demo Replay may be started right now",
    description=(
        "available is False (with a reason) when a real live race is detected. "
        "Does not consider whether a replay is already running — see "
        "/demo/replay/status."
    ),
)
@limiter.limit(rate_limit_value)
async def get_replay_available(
    request: Request,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> ReplayAvailableResponse:
    return await demo_service.get_replay_availability(redis_client)


@router.get(
    "/replay/status",
    response_model=ReplayStatusResponse,
    summary="Current Demo Replay state",
    description=(
        "running True with details (race, lap window, start time) when a "
        "replay is active; running False otherwise."
    ),
)
@limiter.limit(rate_limit_value)
async def get_replay_status(
    request: Request,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> ReplayStatusResponse:
    return await demo_service.get_replay_status(redis_client)


@router.post(
    "/replay/start",
    response_model=ReplayStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a Demo Replay for a curated session",
    description=(
        "Launches replay_pipeline.py for one of the curated sessions. 409 if a "
        "live race is detected or a replay is already running; 422 if "
        "session_id is not curated."
    ),
)
@limiter.limit(rate_limit_value)
async def start_replay(
    request: Request,
    payload: ReplayStartRequest,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> ReplayStartResponse:
    return await demo_service.start_replay(redis_client, payload.session_id)


@router.post(
    "/replay/stop",
    response_model=ReplayStopResponse,
    summary="Stop the running Demo Replay",
    description=(
        "Terminates the replay subprocess and clears its state. 404 if no replay is running."
    ),
)
@limiter.limit(rate_limit_value)
async def stop_replay(
    request: Request,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> ReplayStopResponse:
    return await demo_service.stop_replay(redis_client)
