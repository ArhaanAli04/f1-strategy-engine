"""Registration, login, token refresh, logout, profile, and FCM token routes.

Zero business logic here per CLAUDE.md — every handler just extracts the
authenticated user_id from the access token (get_current_user's payload),
calls one user_service function, and returns its response schema.

Every route carries @limiter.limit(rate_limit_value) — see core/rate_limit.py
for why this must be a per-route decorator rather than one global middleware
default, and why each handler below needs a `request: Request` parameter.
"""

import uuid
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.rate_limit import limiter, rate_limit_value
from backend.core.redis_client import get_redis
from backend.core.security import get_current_user
from backend.schemas.user_schema import (
    FCMTokenUpdate,
    LoginResponse,
    RefreshTokenRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserResponse,
)
from backend.services import user_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(rate_limit_value)
async def register(
    request: Request, payload: UserCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> UserResponse:
    return await user_service.register_user(db, payload.email, payload.password, payload.full_name)


@router.post("/login", response_model=LoginResponse)
@limiter.limit(rate_limit_value)
async def login(
    request: Request,
    payload: UserLogin,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> LoginResponse:
    return await user_service.login_user(db, redis_client, payload.email, payload.password)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit(rate_limit_value)
async def refresh(
    request: Request,
    payload: RefreshTokenRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
) -> TokenResponse:
    return await user_service.refresh_token(db, redis_client, payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(rate_limit_value)
async def logout(
    request: Request,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],  # type: ignore[type-arg]
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> None:
    await user_service.logout_user(redis_client, current_user["sub"])


@router.get("/me", response_model=UserResponse)
@limiter.limit(rate_limit_value)
async def me(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> UserResponse:
    return await user_service.get_user(db, uuid.UUID(current_user["sub"]))


@router.put("/fcm-token", response_model=UserResponse)
@limiter.limit(rate_limit_value)
async def update_fcm_token(
    request: Request,
    payload: FCMTokenUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> UserResponse:
    return await user_service.update_fcm_token(
        db, uuid.UUID(current_user["sub"]), payload.fcm_token
    )
