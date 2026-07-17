import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from backend.schemas.alert_schema import AlertResponse


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    is_active: bool
    subscription_tier: str
    created_at: datetime


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_at: datetime


class LoginResponse(TokenResponse):
    refresh_token: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class FCMTokenUpdate(BaseModel):
    fcm_token: str


class SubscriptionCreate(BaseModel):
    driver_ids: list[uuid.UUID]
    team_ids: list[uuid.UUID]
    alert_types: list[str]


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    driver_ids: list[uuid.UUID]
    team_ids: list[uuid.UUID]
    alert_types: list[str]


__all__ = [
    "AlertResponse",
    "UserCreate",
    "UserResponse",
    "UserLogin",
    "TokenResponse",
    "LoginResponse",
    "RefreshTokenRequest",
    "FCMTokenUpdate",
    "SubscriptionCreate",
    "SubscriptionResponse",
]
