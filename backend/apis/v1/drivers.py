"""Driver, analysis, and lap-history routes. Zero business logic — see driver_service.py.

Every route carries @limiter.limit(rate_limit_value) — see core/rate_limit.py
for why this must be a per-route decorator rather than one global middleware
default, and why each handler below needs a `request: Request` parameter.
"""

import uuid
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.rate_limit import limiter, rate_limit_value
from backend.core.redis_client import get_redis
from backend.schemas.common import PaginatedResponse
from backend.schemas.driver_schema import DriverAnalysisResponse, DriverResponse
from backend.schemas.telemetry_schema import LapDataResponse
from backend.services import driver_service

router = APIRouter(prefix="/drivers", tags=["drivers"])


@router.get(
    "",
    response_model=list[DriverResponse],
    summary="List all drivers on the current grid",
    description=(
        "Returns every driver in the roster, including their team "
        "contract(s) for the current season."
    ),
)
@limiter.limit(rate_limit_value)
async def list_drivers(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> list[DriverResponse]:
    return await driver_service.get_drivers(redis_client, db)


@router.get(
    "/{driver_id}/analysis",
    response_model=DriverAnalysisResponse,
    summary="Get a driver's style fingerprint for a season",
    description=(
        "Returns the driver's season-level driving-style archetype and cluster "
        "(from a population-level PCA→KMeans→UMAP fit), plus season-relative "
        "performance vs. their team average."
    ),
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "example": {
                            "driver_id": "8e2f9c1a-3b7d-4e2a-9f1c-6a5d2b8e4f10",
                            "season": 2025,
                            "archetype": "aggressive",
                            "cluster": 2,
                            "sector_time_variance": -0.42,
                            "tyre_management_index": 1.18,
                            "lap_time_consistency": -0.05,
                            "stint_length_tendency": 0.63,
                            "umap_x": 3.271,
                            "umap_y": -1.845,
                            "performance_vs_team_avg_seconds": -0.184,
                        }
                    }
                }
            }
        }
    },
)
@limiter.limit(rate_limit_value)
async def get_driver_analysis(
    request: Request,
    driver_id: uuid.UUID,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> DriverAnalysisResponse:
    return await driver_service.get_driver_analysis(db, redis_client, driver_id, session_id)


@router.get(
    "/{driver_id}/laps",
    response_model=PaginatedResponse[LapDataResponse],
    summary="Get a driver's lap-by-lap history for a session",
    description=(
        "Returns paginated lap data (times, sectors, compound, tyre age) "
        "for one driver in one session, oldest lap first."
    ),
)
@limiter.limit(rate_limit_value)
async def get_driver_laps(
    request: Request,
    driver_id: uuid.UUID,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginatedResponse[LapDataResponse]:
    return await driver_service.get_driver_laps(
        redis_client, db, driver_id, session_id, page, page_size
    )
