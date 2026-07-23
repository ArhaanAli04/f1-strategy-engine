"""Unit tests for core/security.py — JWT minting/decoding and bcrypt password hashing.

decode_token reads settings via get_auth_settings(), which loads SECRET_KEY from
the repo-root .env (same as every other unit/integration test — see
tests/integration/conftest.py's SECRET_KEY fallback). No monkeypatching needed:
tokens are minted and decoded against the same real dev secret.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt

from backend.core.config import get_auth_settings
from backend.core.exceptions import AuthenticationError, AuthorizationError
from backend.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    require_role,
    verify_password,
)


@pytest.mark.unit
def test_create_access_token_returns_decodable_jwt() -> None:
    token = create_access_token("user-123")

    payload = decode_token(token)

    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"


@pytest.mark.unit
def test_expired_token_raises_authentication_error() -> None:
    settings = get_auth_settings()
    expired_payload = {
        "sub": "user-123",
        "exp": datetime.now(UTC) - timedelta(minutes=5),
        "type": "access",
    }
    token = jwt.encode(expired_payload, settings.secret_key, algorithm=settings.algorithm)

    with pytest.raises(AuthenticationError):
        decode_token(token)


@pytest.mark.unit
def test_invalid_signature_raises_error() -> None:
    settings = get_auth_settings()
    payload = {
        "sub": "user-123",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "type": "access",
    }
    token = jwt.encode(payload, "a-completely-different-wrong-secret", algorithm=settings.algorithm)

    with pytest.raises(AuthenticationError):
        decode_token(token)


@pytest.mark.unit
def test_password_hash_and_verify() -> None:
    hashed = hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False


@pytest.mark.unit
def test_create_access_token_includes_extra_claims() -> None:
    token = create_access_token("user-123", extra={"role": "admin"})

    payload = decode_token(token)

    assert payload["role"] == "admin"


@pytest.mark.unit
def test_create_refresh_token_returns_decodable_jwt() -> None:
    token = create_refresh_token("user-123")

    payload = decode_token(token)

    assert payload["sub"] == "user-123"
    assert payload["type"] == "refresh"


@pytest.mark.unit
async def test_get_current_user_returns_payload_for_valid_access_token() -> None:
    token = create_access_token("user-123")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    payload = await get_current_user(credentials)

    assert payload["sub"] == "user-123"


@pytest.mark.unit
async def test_get_current_user_rejects_non_access_token() -> None:
    token = create_refresh_token("user-123")
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with pytest.raises(AuthenticationError):
        await get_current_user(credentials)


@pytest.mark.unit
async def test_require_role_allows_matching_role() -> None:
    check = require_role("admin", "team")

    result = await check(user={"sub": "user-123", "role": "admin"})

    assert result["role"] == "admin"


@pytest.mark.unit
async def test_require_role_rejects_non_matching_role() -> None:
    check = require_role("admin")

    with pytest.raises(AuthorizationError):
        await check(user={"sub": "user-123", "role": "viewer"})
