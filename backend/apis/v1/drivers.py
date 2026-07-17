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


@router.get("", response_model=list[DriverResponse])
@limiter.limit(rate_limit_value)
async def list_drivers(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> list[DriverResponse]:
    return await driver_service.get_drivers(redis_client, db)


@router.get("/{driver_id}/analysis", response_model=DriverAnalysisResponse)
@limiter.limit(rate_limit_value)
async def get_driver_analysis(
    request: Request,
    driver_id: uuid.UUID,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> DriverAnalysisResponse:
    return await driver_service.get_driver_analysis(db, redis_client, driver_id, session_id)


@router.get("/{driver_id}/laps", response_model=PaginatedResponse[LapDataResponse])
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
