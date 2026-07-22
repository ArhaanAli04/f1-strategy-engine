"""Integration tests for auth flows: register, login, refresh, logout, and
the get_current_user dependency's happy/expired paths — against a real
Postgres + Redis (test_client/authenticated_client fixtures already wired
for this, see conftest.py).

test_protected_endpoint_with_expired_token also covers a same-day fix:
f1_strategy_error_handler previously returned no headers at all on an
AuthenticationError, unlike /metrics's HTTPBasic 401 (main.py's
verify_metrics_auth), which does set WWW-Authenticate. Added a
WWW-Authenticate: Bearer header specifically for AuthenticationError
(RFC 6750) in core/exceptions.py so this test has something real to assert.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_auth_settings
from backend.core.security import verify_password
from backend.models.user import User


async def _fetch_user_by_email(
    db_session_factory: async_sessionmaker[AsyncSession], email: str
) -> User:
    async with db_session_factory() as db:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one()


@pytest.mark.integration
def test_register_creates_user_in_db(
    test_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    email = f"register-{uuid.uuid4()}@example.com"
    password = "RegisterTest123!"  # noqa: S105

    response = test_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Register Test"},
    )

    assert response.status_code == 201
    # Query through test_client's own portal loop, not a fresh asyncio.run()
    # — the register call above already left pooled connections bound to
    # that loop (see seed_via_test_client's docstring for the cross-loop
    # asyncpg hazard this avoids).
    user = test_client.portal.call(_fetch_user_by_email, db_session_factory, email)  # type: ignore[union-attr]
    assert user.hashed_password != password
    assert verify_password(password, user.hashed_password)


@pytest.mark.integration
def test_login_returns_jwt_tokens(test_client: TestClient) -> None:
    email = f"login-{uuid.uuid4()}@example.com"
    password = "LoginTest123!"  # noqa: S105
    test_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Login Test"},
    )

    response = test_client.post("/api/v1/auth/login", json={"email": email, "password": password})

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"  # noqa: S105


@pytest.mark.integration
def test_refresh_token_issues_new_access_token(test_client: TestClient) -> None:
    email = f"refresh-{uuid.uuid4()}@example.com"
    password = "RefreshTest123!"  # noqa: S105
    test_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Refresh Test"},
    )
    login_response = test_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    refresh_token = login_response.json()["refresh_token"]

    response = test_client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})

    assert response.status_code == 200
    new_access_token = response.json()["access_token"]
    assert new_access_token

    # Prove it's a genuinely usable access token rather than just asserting
    # string inequality against the login-issued one — exp is second-
    # granularity, so two tokens minted within the same wall-clock second
    # would otherwise be byte-identical and make that comparison flaky.
    me_response = test_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {new_access_token}"}
    )
    assert me_response.status_code == 200


@pytest.mark.integration
def test_logout_invalidates_refresh_token(test_client: TestClient) -> None:
    email = f"logout-{uuid.uuid4()}@example.com"
    password = "LogoutTest123!"  # noqa: S105
    test_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": "Logout Test"},
    )
    login_response = test_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    access_token = login_response.json()["access_token"]
    refresh_token = login_response.json()["refresh_token"]

    logout_response = test_client.post(
        "/api/v1/auth/logout", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert logout_response.status_code == 204

    refresh_response = test_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert refresh_response.status_code == 401


@pytest.mark.integration
def test_protected_endpoint_with_valid_token(authenticated_client: TestClient) -> None:
    response = authenticated_client.get("/api/v1/auth/me")

    assert response.status_code == 200
    body = response.json()
    assert body["email"]
    assert body["is_active"] is True


@pytest.mark.integration
def test_protected_endpoint_with_expired_token(test_client: TestClient) -> None:
    settings = get_auth_settings()
    expired_payload = {
        "sub": str(uuid.uuid4()),
        "exp": datetime.now(UTC) - timedelta(minutes=1),
        "type": "access",
    }
    expired_token = jwt.encode(expired_payload, settings.secret_key, algorithm=settings.algorithm)

    response = test_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"}
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
