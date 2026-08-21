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


@router.get(
    "",
    response_model=list[AlertResponse],
    summary="List the current user's alerts",
    description=(
        "Returns this user's alert history (undercut threats, pit window "
        "opens, safety car probability, etc.), newest first. Pass "
        "unread=true to return only alerts that haven't been marked read."
    ),
)
@limiter.limit(rate_limit_value)
async def get_alerts(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    unread: bool = Query(False),
) -> list[AlertResponse]:
    return await alert_service.get_user_alerts(db, uuid.UUID(current_user["sub"]), unread)


@router.put(
    "/{alert_id}/read",
    response_model=AlertResponse,
    summary="Mark one alert as read",
    description=(
        "Sets read_at on the given alert for the current user and returns the updated alert."
    ),
)
@limiter.limit(rate_limit_value)
async def mark_alert_read(
    request: Request,
    alert_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> AlertResponse:
    return await alert_service.mark_alert_read(db, uuid.UUID(current_user["sub"]), alert_id)


@router.get(
    "/subscriptions",
    response_model=SubscriptionResponse,
    summary="Get the current user's alert subscription preferences",
    description="Returns which drivers, teams, and alert types the current user is subscribed to.",
)
@limiter.limit(rate_limit_value)
async def get_subscriptions(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> SubscriptionResponse:
    return await alert_service.get_subscription(db, uuid.UUID(current_user["sub"]))


@router.put(
    "/subscriptions",
    response_model=SubscriptionResponse,
    summary="Replace the current user's alert subscription preferences",
    description=(
        "Overwrites (not merges) the current user's driver/team/alert-type "
        "subscription lists — send the full desired state each time."
    ),
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": {
                        "driver_ids": ["8e2f9c1a-3b7d-4e2a-9f1c-6a5d2b8e4f10"],
                        "team_ids": ["1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"],
                        "alert_types": [
                            "UNDERCUT_THREAT",
                            "PIT_WINDOW_OPEN",
                            "SAFETY_CAR_PROBABILITY",
                        ],
                    }
                }
            }
        }
    },
)
@limiter.limit(rate_limit_value)
async def update_subscriptions(
    request: Request,
    payload: SubscriptionCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> SubscriptionResponse:
    return await alert_service.update_subscription(db, uuid.UUID(current_user["sub"]), payload)
