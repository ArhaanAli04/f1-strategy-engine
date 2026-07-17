"""Pit-window, undercut, strategy-overview, and race-simulation routes.

Zero business logic — see strategy_service.py — except POST /simulate and
GET /simulate/{task_id}, which bridge to Celery (see module docstring on
prediction_worker.run_race_simulation for why that task itself has no
service-layer home: it isn't a cache-aside DB/Redis computation like the rest
of strategy_service.py, it's an async-to-sync task-queue dispatch/poll, which
is infrastructure glue at the API boundary, not business logic).

Every route carries @limiter.limit(rate_limit_value) — see core/rate_limit.py
for why this must be a per-route decorator rather than one global middleware
default, and why each handler below needs a `request: Request` parameter.
"""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated

import redis.asyncio as aioredis
from celery.result import AsyncResult
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.rate_limit import limiter, rate_limit_value
from backend.core.redis_client import get_redis
from backend.schemas.simulate_schema import (
    SimulateStrategyRequest,
    SimulateStrategyResponse,
    SimulateTaskAccepted,
    SimulateTaskStatusResponse,
)
from backend.schemas.strategy_schema import (
    PitWindowResponse,
    StrategyOverviewResponse,
    UndercutThreatResponse,
)
from backend.services import strategy_service
from backend.workers.celery_app import app as celery_app
from backend.workers.prediction_worker import run_race_simulation

router = APIRouter(prefix="/strategy", tags=["strategy"])

# Dedicated executor for the .delay() hop below, instead of asyncio's shared
# default ThreadPoolExecutor (run_in_executor(None, ...)) — the default pool
# is capped at min(32, cpu_count+4), which measured at 20 threads on this
# container and was the actual bottleneck behind /simulate's ~12-14s enqueue
# latency at 100 concurrent users (see CLAUDE.md's Deferred Wiring: raising
# Celery's broker_pool_limit 10->50 did not fix it). Sized to match that same
# broker_pool_limit=50 (workers/celery_app.py) — more threads than available
# broker connections would just queue on the connection instead of the thread.
_SIMULATE_ENQUEUE_EXECUTOR = ThreadPoolExecutor(
    max_workers=50, thread_name_prefix="simulate-enqueue"
)


# Registered ahead of the /{session_id}/... routes below: session_id is
# uuid.UUID-typed, so a literal "simulate" first segment already fails that
# conversion and falls through correctly regardless of order — but declaring
# the static-prefix route first is the safer, more explicit convention.
@router.get("/simulate/{task_id}", response_model=SimulateTaskStatusResponse)
@limiter.limit(rate_limit_value)
async def get_simulation_result(request: Request, task_id: str) -> SimulateTaskStatusResponse:
    result = AsyncResult(task_id, app=celery_app)
    parsed_result = (
        SimulateStrategyResponse.model_validate(result.result) if result.successful() else None
    )
    return SimulateTaskStatusResponse(task_id=task_id, status=result.status, result=parsed_result)


@router.post(
    "/{session_id}/simulate",
    response_model=SimulateTaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit(rate_limit_value)
async def simulate_strategy(
    request: Request, session_id: uuid.UUID, payload: SimulateStrategyRequest
) -> SimulateTaskAccepted:
    task_payload = {"session_id": str(session_id), **payload.model_dump(mode="json")}
    # .delay() is a quick synchronous Redis broker call, not the simulation
    # itself (that runs in a separate Celery worker process) — but it's still
    # blocking I/O, so it's offloaded to a thread rather than run directly on
    # the event loop. Uses a dedicated executor, not the shared asyncio
    # default — see _SIMULATE_ENQUEUE_EXECUTOR above.
    loop = asyncio.get_running_loop()
    task = await loop.run_in_executor(
        _SIMULATE_ENQUEUE_EXECUTOR, run_race_simulation.delay, task_payload
    )
    return SimulateTaskAccepted(task_id=task.id, status=task.status)


@router.get("/{session_id}/{driver_id}/pit-window", response_model=list[PitWindowResponse])
@limiter.limit(rate_limit_value)
async def get_pit_window(
    request: Request,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> list[PitWindowResponse]:
    return await strategy_service.get_pit_window_for_session(
        redis_client, db, session_id, driver_id
    )


@router.get("/{session_id}/{driver_id}/undercut", response_model=UndercutThreatResponse)
@limiter.limit(rate_limit_value)
async def get_undercut(
    request: Request,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    target: uuid.UUID = Query(..., description="Rival driver_id being undercut"),  # noqa: B008
) -> UndercutThreatResponse:
    return await strategy_service.get_undercut_for_session(
        redis_client, db, session_id, driver_id, target
    )


@router.get("/{session_id}/overview", response_model=StrategyOverviewResponse)
@limiter.limit(rate_limit_value)
async def get_strategy_overview(
    request: Request,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> StrategyOverviewResponse:
    return await strategy_service.get_strategy_overview_for_session(redis_client, db, session_id)
