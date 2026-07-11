"""Registration, login, token refresh, logout, and subscription management.

Refresh tokens use a single-active-session model: Redis key
f1:auth:refresh:{user_id} holds the one currently-valid refresh token for
that user. Logging in again (any device) overwrites it, invalidating
whatever refresh token was issued before. logout deletes the key outright.
refresh_token checks the presented token against the stored value, so a
deleted or superseded token is rejected even though the JWT itself is still
cryptographically valid until its own exp claim.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_auth_settings
from backend.core.exceptions import AuthenticationError, ConflictError, NotFoundError
from backend.core.redis_client import redis_delete, redis_get, redis_set
from backend.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from backend.models.user import Subscription, User
from backend.schemas.user_schema import (
    LoginResponse,
    SubscriptionCreate,
    SubscriptionResponse,
    TokenResponse,
    UserResponse,
)

TOKEN_TYPE = "bearer"  # noqa: S105 — this is an OAuth2 scheme name, not a secret


def _refresh_key(user_id: uuid.UUID | str) -> str:
    return f"f1:auth:refresh:{user_id}"


def _token_expiry(token: str) -> datetime:
    """Read the exp claim off a freshly-minted token, so the response's
    expires_at always matches what the token itself actually encodes.
    """
    payload = decode_token(token)
    return datetime.fromtimestamp(payload["exp"], tz=UTC)


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> UserResponse:
    """Fetch a user by ID.

    Args:
        db: Async DB session.
        user_id: The user to fetch.
    Returns:
        The user.
    Raises:
        NotFoundError: If no user with this ID exists.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found")
    return UserResponse.model_validate(user)


async def register_user(
    db: AsyncSession, email: str, password: str, full_name: str
) -> UserResponse:
    """Create a new user with a bcrypt-hashed password.

    Args:
        db: Async DB session.
        email: User's email — must be unique.
        password: Plaintext password to hash before storage.
        full_name: User's display name.
    Returns:
        The newly created user.
    Raises:
        ConflictError: If a user with this email already exists.
    """
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"Email '{email}' is already registered")

    user = User(email=email, hashed_password=hash_password(password), full_name=full_name)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)


async def login_user(
    db: AsyncSession,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    email: str,
    password: str,
) -> LoginResponse:
    """Verify credentials and issue a new access/refresh token pair.

    Args:
        db: Async DB session.
        redis_client: Redis client the refresh token is stored against.
        email: User's email.
        password: Plaintext password to verify against the stored hash.
    Returns:
        Access token, refresh token, and the access token's expiry.
    Raises:
        AuthenticationError: If the credentials are invalid or the account is inactive.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.hashed_password):
        raise AuthenticationError("Invalid email or password")
    if not user.is_active:
        raise AuthenticationError("User account is inactive")

    subject = str(user.id)
    access_token = create_access_token(subject)
    refresh_token = create_refresh_token(subject)

    settings = get_auth_settings()
    await redis_set(
        redis_client,
        _refresh_key(user.id),
        refresh_token,
        ttl=settings.refresh_token_expire_days * 86400,
    )

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=TOKEN_TYPE,
        expires_at=_token_expiry(access_token),
    )


async def refresh_token(
    db: AsyncSession,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    token: str,
) -> TokenResponse:
    """Validate a refresh token against Redis and issue a new access token.

    Args:
        db: Async DB session.
        redis_client: Redis client the refresh token is checked against.
        token: The refresh token presented by the client.
    Returns:
        A new access token; the refresh token itself is not rotated.
    Raises:
        AuthenticationError: If the token is malformed, expired, superseded
            by a later login, deleted by logout, or its user is inactive.
    """
    payload = decode_token(token)
    if payload.get("type") != "refresh":
        raise AuthenticationError("Token is not a refresh token")

    user_id = payload["sub"]
    stored = await redis_get(redis_client, _refresh_key(user_id))
    if stored is None or stored != token:
        raise AuthenticationError("Refresh token has been invalidated")

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthenticationError("User account is inactive")

    access_token = create_access_token(user_id)
    return TokenResponse(
        access_token=access_token,
        token_type=TOKEN_TYPE,
        expires_at=_token_expiry(access_token),
    )


async def logout_user(redis_client: aioredis.Redis, user_id: uuid.UUID | str) -> None:  # type: ignore[type-arg]
    """Invalidate a user's refresh token.

    Args:
        redis_client: Redis client the refresh token is stored against.
        user_id: The user whose session should be invalidated.
    Returns:
        None.
    """
    await redis_delete(redis_client, _refresh_key(user_id))


async def update_subscription(
    db: AsyncSession, user_id: uuid.UUID, subscription: SubscriptionCreate
) -> SubscriptionResponse:
    """Create or replace a user's driver/team/alert-type tracking subscription.

    Args:
        db: Async DB session.
        user_id: The subscribing user.
        subscription: Driver IDs, team IDs, and alert types to track.
    Returns:
        The updated subscription.
    """
    result = await db.execute(select(Subscription).where(Subscription.user_id == user_id))
    existing = result.scalar_one_or_none()

    if existing is None:
        existing = Subscription(user_id=user_id)
        db.add(existing)

    existing.driver_ids = [str(d) for d in subscription.driver_ids]
    existing.team_ids = [str(t) for t in subscription.team_ids]
    existing.alert_types = list(subscription.alert_types)

    await db.commit()
    await db.refresh(existing)
    return SubscriptionResponse.model_validate(existing)


async def update_fcm_token(db: AsyncSession, user_id: uuid.UUID, fcm_token: str) -> UserResponse:
    """Register a device's FCM token for push notification delivery.

    Args:
        db: Async DB session.
        user_id: The user registering their device.
        fcm_token: Firebase Cloud Messaging device token.
    Returns:
        The updated user.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found")

    user.fcm_token = fcm_token
    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)
