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

All routes except GET /simulate/{task_id} require Depends(get_current_user):
these are the compute-heavy ML inference/simulation endpoints (previously
public — see CLAUDE.md's Deferred Wiring). GET /simulate/{task_id} stays
unauthenticated: it's a cheap Celery result lookup keyed by an unguessable
task UUID, not a computation itself.
"""

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any

import redis.asyncio as aioredis
from celery.result import AsyncResult
from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.rate_limit import limiter, rate_limit_value
from backend.core.redis_client import get_redis
from backend.core.security import get_current_user
from backend.schemas.simulate_schema import (
    SimulateStrategyRequest,
    SimulateStrategyResponse,
    SimulateTaskAccepted,
    SimulateTaskStatusResponse,
)
from backend.schemas.strategy_schema import (
    LastIngestedSessionResponse,
    PitWindowResponse,
    StrategyOverviewResponse,
    StrategyPredictionHistoryResponse,
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
@router.get(
    "/simulate/{task_id}",
    response_model=SimulateTaskStatusResponse,
    summary="Poll a race simulation task for its result",
    description=(
        "Polls the Celery result backend for a task_id returned by POST "
        "/{session_id}/simulate. status is PENDING/STARTED while running; "
        "result is populated only once status is SUCCESS."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": {
                            "task_id": "3f9c1e2a-7b4d-4e6a-8c1f-2a5d9b8e4c10",
                            "status": "SUCCESS",
                            "result": {
                                "driver_id": "8e2f9c1a-3b7d-4e2a-9f1c-6a5d2b8e4f10",
                                "strategies": [
                                    {
                                        "pit_laps": [22, 41],
                                        "compounds": ["MEDIUM", "HARD"],
                                        "predicted_finish_time": 5423.7,
                                        "position_gain_loss": 1,
                                        "confidence_interval": [5401.2, 5449.8],
                                        "explanation": {
                                            "pit_cost_seconds": 22.5,
                                            "drivers_overtaken": [
                                                {
                                                    "position": 7,
                                                    "driver_id": (
                                                        "2c6b1f8e-4a3d-4b2c-9e7f-1d8a5c3b6f42"
                                                    ),
                                                    "gap_seconds": 3.2,
                                                }
                                            ],
                                            "remaining_laps": 30,
                                            "fresh_tyre_gain_per_lap": 0.41,
                                            "total_recoverable_seconds": 12.3,
                                        },
                                    }
                                ],
                            },
                        }
                    }
                }
            }
        }
    },
)
@limiter.limit(rate_limit_value)
async def get_simulation_result(request: Request, task_id: str) -> SimulateTaskStatusResponse:
    result = AsyncResult(task_id, app=celery_app)
    parsed_result = (
        SimulateStrategyResponse.model_validate(result.result) if result.successful() else None
    )
    return SimulateTaskStatusResponse(task_id=task_id, status=result.status, result=parsed_result)


# Static-prefix route, registered ahead of the /{session_id}/... routes below
# — same convention as /simulate/{task_id} above.
@router.get(
    "/last-ingested-session",
    response_model=LastIngestedSessionResponse,
    summary="Most recently ingested completed race session (by race date)",
    description=(
        "The COMPLETED R session with the newest race_date among sessions "
        "that have ingested lap data. Used by the Strategy Simulator as its "
        "session source when no race is live — resolved per-environment "
        "from that environment's own DB. Excludes scheduled/in-progress "
        "sessions (e.g. a partial live-ingestion dry run) whose lap_data "
        "may have NULL position or missing laps. 404 only when no completed "
        "R session with lap data exists yet."
    ),
)
@limiter.limit(rate_limit_value)
async def get_last_ingested_session(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> LastIngestedSessionResponse:
    return await strategy_service.get_last_ingested_session(redis_client, db)


@router.post(
    "/{session_id}/simulate",
    response_model=SimulateTaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a Monte Carlo race strategy simulation",
    description=(
        "Enqueues a 1000-run Monte Carlo simulation (Celery task) for one driver "
        "at their current race state. Leave pit_laps empty to let the simulation "
        "decide pit timing autonomously, or set pit_laps + compounds to force a "
        "specific what-if pit plan. Returns immediately with a task_id — poll "
        "GET /simulate/{task_id} for the result. current_lap must be at most one "
        "lap past this session's real ingested progress (404 if the session "
        "doesn't exist, 422 if current_lap is implausibly far ahead)."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "autonomous": {
                            "summary": "Let the simulation decide pit timing",
                            "value": {
                                "driver_id": "8e2f9c1a-3b7d-4e2a-9f1c-6a5d2b8e4f10",
                                "current_lap": 18,
                                "current_compound": "MEDIUM",
                                "current_tyre_age": 12,
                                "remaining_laps": 40,
                                "pit_laps": [],
                                "compounds": [],
                            },
                        },
                        "what_if_forced_pit": {
                            "summary": "Force a two-stop plan (MEDIUM then HARD)",
                            "value": {
                                "driver_id": "8e2f9c1a-3b7d-4e2a-9f1c-6a5d2b8e4f10",
                                "current_lap": 18,
                                "current_compound": "MEDIUM",
                                "current_tyre_age": 12,
                                "remaining_laps": 40,
                                "pit_laps": [22, 41],
                                "compounds": ["MEDIUM", "HARD"],
                            },
                        },
                    }
                }
            }
        }
    },
)
@limiter.limit(rate_limit_value)
async def simulate_strategy(
    request: Request,
    session_id: uuid.UUID,
    payload: SimulateStrategyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> SimulateTaskAccepted:
    # Reject before enqueueing anything — a bad current_lap should never
    # cost a Celery round trip, and the caller gets a synchronous 404/422
    # instead of having to poll a task to FAILURE to find out. See
    # docs/simulator-issues-wet-model-and-position-context.md's Checkpoint-6
    # follow-up finding and strategy_service.validate_current_lap's own
    # docstring for what this actually checks and why. Also enforced
    # independently inside prediction_worker._run_simulation (defense in
    # depth) — a caller that dispatches run_race_simulation directly,
    # bypassing this route, must not be able to skip it.
    await strategy_service.validate_current_lap(db, session_id, payload.current_lap)

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


@router.get(
    "/{session_id}/{driver_id}/pit-window",
    response_model=list[PitWindowResponse],
    summary="Get predicted optimal pit windows for a driver",
    description=(
        "Returns predicted pit lap(s) with a projected total time delta and, "
        "when available, the top SHAP feature contributions behind the "
        "prediction (tyre age, gap to rivals, safety car probability, etc.)."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": [
                            {
                                "pit_lap": 24,
                                "window_start": 22,
                                "window_end": 26,
                                "projected_total_delta_seconds": -4.8,
                                "shap_explanation": [
                                    {
                                        "feature_name": "predicted_life_remaining",
                                        "value": 3.0,
                                        "contribution": 0.31,
                                        "direction": "+",
                                    },
                                    {
                                        "feature_name": "safety_car_probability",
                                        "value": 0.12,
                                        "contribution": -0.05,
                                        "direction": "-",
                                    },
                                ],
                            }
                        ]
                    }
                }
            }
        }
    },
)
@limiter.limit(rate_limit_value)
async def get_pit_window(
    request: Request,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> list[PitWindowResponse]:
    return await strategy_service.get_pit_window_for_session(
        redis_client, db, session_id, driver_id
    )


@router.get(
    "/{session_id}/{driver_id}/undercut",
    response_model=UndercutThreatResponse,
    summary="Get undercut threat probability against a rival",
    description=(
        "Returns the probability that pitting now gains track position over "
        "the target rival, plus the projected gap."
    ),
)
@limiter.limit(rate_limit_value)
async def get_undercut(
    request: Request,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    target: uuid.UUID = Query(..., description="Rival driver_id being undercut"),  # noqa: B008
) -> UndercutThreatResponse:
    return await strategy_service.get_undercut_for_session(
        redis_client, db, session_id, driver_id, target
    )


@router.get(
    "/{session_id}/{driver_id}/history",
    response_model=StrategyPredictionHistoryResponse,
    summary="Get a driver's full StrategyPrediction history for a session",
    description=(
        "Returns every persisted prediction for one driver in this session, "
        "ordered by lap ascending — the progression over time, as opposed to "
        "/overview which is always the live/current state for the whole field."
    ),
)
@limiter.limit(rate_limit_value)
async def get_strategy_prediction_history(
    request: Request,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> StrategyPredictionHistoryResponse:
    return await strategy_service.get_strategy_prediction_history_for_session(
        db, session_id, driver_id
    )


@router.get(
    "/{session_id}/overview",
    response_model=StrategyOverviewResponse,
    summary="Get predicted pit strategy for every driver in a session",
    description=(
        "Returns each driver's predicted pit lap and pit probability for the "
        "whole field — the compute-heaviest endpoint (cache-aside, ~16-17s "
        "on a cold miss)."
    ),
)
@limiter.limit(rate_limit_value)
async def get_strategy_overview(
    request: Request,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> StrategyOverviewResponse:
    return await strategy_service.get_strategy_overview_for_session(redis_client, db, session_id)
