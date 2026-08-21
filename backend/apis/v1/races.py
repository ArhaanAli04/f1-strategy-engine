"""Race, session, and current-race routes. Zero business logic — see race_service.py.

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
from backend.schemas.race_schema import (
    RaceListResponse,
    RaceResponse,
    SessionResponse,
    UpcomingRaceResponse,
)
from backend.services import race_service

router = APIRouter(prefix="/races", tags=["races"])


@router.get(
    "/current",
    response_model=RaceResponse,
    summary="Get the race currently in progress",
    description=(
        "Resolves the current season/round via Ergast and returns that race "
        "with its circuit and sessions. 404 if no race is currently live."
    ),
)
@limiter.limit(rate_limit_value)
async def get_current_race(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> RaceResponse:
    return await race_service.get_current_race(redis_client, db)


# Registered ahead of /{race_id} (same reason as /current above) so a literal
# "upcoming" segment never gets swallowed by the UUID path param.
@router.get(
    "/upcoming",
    response_model=UpcomingRaceResponse,
    summary="Get the next scheduled race",
    description=(
        "Returns the minimal race/circuit/start-time shape used by the "
        "Circuit Map Panel's non-race countdown mode."
    ),
)
@limiter.limit(rate_limit_value)
async def get_upcoming_race(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> UpcomingRaceResponse:
    return await race_service.get_upcoming_race(redis_client, db)


@router.get(
    "",
    response_model=PaginatedResponse[RaceListResponse],
    summary="List races, optionally filtered by season/round",
    description=(
        "Returns a paginated list of races with their circuit info. "
        "Filter with season and/or round query params."
    ),
)
@limiter.limit(rate_limit_value)
async def list_races(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    season: int | None = Query(None),
    round_number: int | None = Query(None, alias="round"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginatedResponse[RaceListResponse]:
    return await race_service.get_races(
        redis_client, db, season=season, round_number=round_number, page=page, page_size=page_size
    )


@router.get(
    "/{race_id}",
    response_model=RaceResponse,
    summary="Get one race by ID",
    description="Returns a single race with its circuit and all sessions.",
)
@limiter.limit(rate_limit_value)
async def get_race(
    request: Request,
    race_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> RaceResponse:
    return await race_service.get_race(redis_client, db, race_id)


@router.get(
    "/{race_id}/sessions/{session_id}",
    response_model=SessionResponse,
    summary="Get one session of a race",
    description=(
        "Returns a single session (e.g. FP1, Qualifying, Race) belonging to the given race."
    ),
)
@limiter.limit(rate_limit_value)
async def get_session(
    request: Request,
    race_id: uuid.UUID,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> SessionResponse:
    return await race_service.get_session(redis_client, db, race_id, session_id)
