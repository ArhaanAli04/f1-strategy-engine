"""Circuit outline routes. Zero business logic — see circuit_service.py."""

import uuid
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.rate_limit import limiter, rate_limit_value
from backend.core.redis_client import get_redis
from backend.schemas.circuit_schema import CircuitOutlineResponse
from backend.services import circuit_service

router = APIRouter(prefix="/circuits", tags=["circuits"])


@router.get("/{circuit_id}/outline", response_model=CircuitOutlineResponse)
@limiter.limit(rate_limit_value)
async def get_circuit_outline(
    request: Request,
    circuit_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> CircuitOutlineResponse:
    return await circuit_service.get_circuit_outline(redis_client, db, circuit_id)
