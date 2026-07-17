import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import bcrypt
from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from backend.core.exceptions import AuthenticationError, AuthorizationError

logger = logging.getLogger(__name__)

_bearer = HTTPBearer()


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of the plaintext password."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches the stored *hashed* password."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    """Mint a short-lived JWT access token for *subject*."""
    from backend.core.config import get_auth_settings

    settings = get_auth_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": "access"}
    if extra:
        payload.update(extra)
    return str(jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm))


def create_refresh_token(subject: str) -> str:
    """Mint a long-lived JWT refresh token for *subject*."""
    from backend.core.config import get_auth_settings

    settings = get_auth_settings()
    expire = datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)
    payload: dict[str, Any] = {"sub": subject, "exp": expire, "type": "refresh"}
    return str(jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm))


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify *token*, raising AuthenticationError on any failure."""
    from backend.core.config import get_auth_settings

    settings = get_auth_settings()
    try:
        result: dict[str, Any] = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
        return result
    except JWTError as exc:
        raise AuthenticationError("Invalid or expired token") from exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),  # noqa: B008
) -> dict[str, Any]:
    """FastAPI dependency — validates Bearer token and returns the decoded payload."""
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise AuthenticationError("Token is not an access token")
    return payload


def require_role(*roles: str) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
    """Return a FastAPI dependency factory that enforces one of *roles*."""

    async def _check(
        user: Annotated[dict[str, Any], Depends(get_current_user)],
    ) -> dict[str, Any]:
        user_role: str = str(user.get("role", ""))
        if user_role not in roles:
            raise AuthorizationError(
                f"Role '{user_role}' not permitted. Required one of: {list(roles)}"
            )
        return user

    return _check
