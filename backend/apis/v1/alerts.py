"""Alert history and subscription-preference routes. Zero business logic — see alert_service.py.

Every route carries @limiter.limit(rate_limit_value) — see core/rate_limit.py
for why this must be a per-route decorator rather than one global middleware
default, and why each handler below needs a `request: Request` parameter.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.rate_limit import limiter, rate_limit_value
from backend.core.security import get_current_user
from backend.schemas.alert_schema import AlertResponse
from backend.schemas.user_schema import SubscriptionCreate, SubscriptionResponse
from backend.services import alert_service

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertResponse])
@limiter.limit(rate_limit_value)
async def get_alerts(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    unread: bool = Query(False),
) -> list[AlertResponse]:
    return await alert_service.get_user_alerts(db, uuid.UUID(current_user["sub"]), unread)


@router.put("/{alert_id}/read", response_model=AlertResponse)
@limiter.limit(rate_limit_value)
async def mark_alert_read(
    request: Request,
    alert_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> AlertResponse:
    return await alert_service.mark_alert_read(db, uuid.UUID(current_user["sub"]), alert_id)


@router.get("/subscriptions", response_model=SubscriptionResponse)
@limiter.limit(rate_limit_value)
async def get_subscriptions(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> SubscriptionResponse:
    return await alert_service.get_subscription(db, uuid.UUID(current_user["sub"]))


@router.put("/subscriptions", response_model=SubscriptionResponse)
@limiter.limit(rate_limit_value)
async def update_subscriptions(
    request: Request,
    payload: SubscriptionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> SubscriptionResponse:
    return await alert_service.update_subscription(db, uuid.UUID(current_user["sub"]), payload)
