"""Race, session, and current-race routes. Zero business logic — see race_service.py.

Every route carries @limiter.limit(rate_limit_value) — see core/rate_limit.py
for why this must be a per-route decorator rather than one global middleware
default, and why each handler below needs a `request: Request` parameter.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.rate_limit import limiter, rate_limit_value
from backend.schemas.common import PaginatedResponse
from backend.schemas.race_schema import RaceListResponse, RaceResponse, SessionResponse
from backend.services import race_service

router = APIRouter(prefix="/races", tags=["races"])


@router.get("/current", response_model=RaceResponse)
@limiter.limit(rate_limit_value)
async def get_current_race(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> RaceResponse:
    return await race_service.get_current_race(db)


@router.get("", response_model=PaginatedResponse[RaceListResponse])
@limiter.limit(rate_limit_value)
async def list_races(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    season: int | None = Query(None),
    round_number: int | None = Query(None, alias="round"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PaginatedResponse[RaceListResponse]:
    return await race_service.get_races(
        db, season=season, round_number=round_number, page=page, page_size=page_size
    )


@router.get("/{race_id}", response_model=RaceResponse)
@limiter.limit(rate_limit_value)
async def get_race(
    request: Request, race_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> RaceResponse:
    return await race_service.get_race(db, race_id)


@router.get("/{race_id}/sessions/{session_id}", response_model=SessionResponse)
@limiter.limit(rate_limit_value)
async def get_session(
    request: Request,
    race_id: uuid.UUID,
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionResponse:
    return await race_service.get_session(db, race_id, session_id)
