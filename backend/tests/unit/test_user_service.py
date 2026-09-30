"""Unit tests for services/user_service.py's registration (mock DB session)."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.exceptions import ConflictError
from backend.models.user import Subscription, User
from backend.schemas.alert_schema import AlertType
from backend.services import user_service


def _no_existing_user() -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    return result


def _driver_ids(*ids: uuid.UUID) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(ids)
    return result


def _fill_in_database_defaults(db: AsyncMock) -> None:
    """What flush/refresh do for real: give the new user its id and defaults."""

    async def flush() -> None:
        for call in db.add.call_args_list:
            added = call.args[0]
            if isinstance(added, User) and added.id is None:
                added.id = uuid.uuid4()

    async def refresh(user: Any) -> None:
        user.is_active = True
        user.subscription_tier = "free"
        user.created_at = datetime.now(UTC)

    db.flush.side_effect = flush
    db.refresh.side_effect = refresh


@pytest.mark.unit
async def test_registration_subscribes_the_user_to_undercut_alerts_on_every_driver(
    mock_db_session: AsyncMock,
) -> None:
    ver, lec = uuid.uuid4(), uuid.uuid4()
    mock_db_session.execute.side_effect = [_no_existing_user(), _driver_ids(ver, lec)]
    _fill_in_database_defaults(mock_db_session)

    user = await user_service.register_user(
        mock_db_session, "fan@example.com", "S3cure-test-only!", "A Fan"
    )

    added = [call.args[0] for call in mock_db_session.add.call_args_list]
    (subscription,) = [row for row in added if isinstance(row, Subscription)]
    assert subscription.user_id == user.id
    assert subscription.driver_ids == [str(ver), str(lec)]
    assert subscription.alert_types == [AlertType.UNDERCUT_THREAT.value]
    assert subscription.team_ids == []
    mock_db_session.commit.assert_awaited_once()  # user and subscription together


@pytest.mark.unit
async def test_registration_with_a_taken_email_creates_nothing(
    mock_db_session: AsyncMock,
) -> None:
    taken = MagicMock()
    taken.scalar_one_or_none.return_value = User(email="fan@example.com")
    mock_db_session.execute.side_effect = [taken]

    with pytest.raises(ConflictError):
        await user_service.register_user(
            mock_db_session, "fan@example.com", "S3cure-test-only!", "A Fan"
        )

    mock_db_session.add.assert_not_called()
    mock_db_session.commit.assert_not_awaited()
