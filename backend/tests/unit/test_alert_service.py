"""Unit tests for services/alert_service.py — threat evaluation and alert dispatch.

mock_db_session (AsyncMock spec'd to AsyncSession) stands in for the DB; fakeredis
stands in for Redis so dispatch_alert's real client.publish() runs, not a mock,
letting tests assert on the channel/payload actually published.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis as fakeredis_lib
import pytest

from backend.core.exceptions import NotFoundError
from backend.models.telemetry import LapData
from backend.models.user import Alert, Subscription
from backend.schemas.alert_schema import AlertType
from backend.schemas.user_schema import SubscriptionCreate
from backend.services import alert_service


def _fake_position(driver_id: uuid.UUID, position: int) -> MagicMock:
    lap = MagicMock(spec=LapData)
    lap.driver_id = driver_id
    lap.position = position
    return lap


def _scalars_all_result(items: list[Any]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _rows_result(rows: list[Any]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


def _scalar_one_or_none_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_first_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.first.return_value = value
    return result


def _fake_alert(user_id: uuid.UUID, session_id: uuid.UUID, read_at: datetime | None) -> Any:
    return MagicMock(
        spec=Alert,
        id=uuid.uuid4(),
        user_id=user_id,
        session_id=session_id,
        alert_type=AlertType.UNDERCUT_THREAT.value,
        driver_id=None,
        message="Undercut threat",
        triggered_at=datetime.now(UTC),
        delivered_at=None,
        read_at=read_at,
    )


@pytest.mark.unit
async def test_undercut_threat_fires_alert_above_threshold(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    session_id = uuid.uuid4()
    leader_id = uuid.uuid4()
    trailing_id = uuid.uuid4()
    subscriber_id = uuid.uuid4()

    positions = [_fake_position(leader_id, 1), _fake_position(trailing_id, 2)]
    score_row = MagicMock(driver_id=trailing_id, undercut_score=0.75)
    subscriber_row = MagicMock(user_id=subscriber_id)

    mock_db_session.execute.side_effect = [
        _scalars_all_result(positions),
        _rows_result([score_row]),
        _rows_result([subscriber_row]),
    ]

    dispatched = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert len(dispatched) == 1
    assert dispatched[0]["driver_id"] == str(trailing_id)
    mock_db_session.add.assert_called_once()


@pytest.mark.unit
async def test_no_alert_below_threshold(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    session_id = uuid.uuid4()
    leader_id = uuid.uuid4()
    trailing_id = uuid.uuid4()

    positions = [_fake_position(leader_id, 1), _fake_position(trailing_id, 2)]
    score_row = MagicMock(driver_id=trailing_id, undercut_score=0.50)

    mock_db_session.execute.side_effect = [
        _scalars_all_result(positions),
        _rows_result([score_row]),
    ]

    dispatched = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert dispatched == []
    mock_db_session.add.assert_not_called()


@pytest.mark.unit
async def test_alert_written_to_db(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    user_id = uuid.uuid4()
    payload = {
        "session_id": str(session_id),
        "driver_id": str(driver_id),
        "message": "Undercut threat",
    }

    await alert_service.dispatch_alert(
        mock_db_session, fakeredis, [user_id], AlertType.UNDERCUT_THREAT, payload
    )

    mock_db_session.add.assert_called_once()
    added = mock_db_session.add.call_args.args[0]
    assert isinstance(added, Alert)
    assert added.user_id == user_id
    assert added.session_id == session_id
    assert added.driver_id == driver_id
    assert added.alert_type == AlertType.UNDERCUT_THREAT.value
    mock_db_session.commit.assert_awaited_once()


@pytest.mark.unit
async def test_alert_published_to_redis_pubsub(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    user_id = uuid.uuid4()
    payload = {
        "session_id": str(session_id),
        "driver_id": str(driver_id),
        "message": "Undercut threat",
    }

    pubsub = fakeredis.pubsub()
    channel = f"f1:alerts:{session_id}"
    await pubsub.subscribe(channel)
    await pubsub.get_message(timeout=1)  # discard the subscribe confirmation

    created = await alert_service.dispatch_alert(
        mock_db_session, fakeredis, [user_id], AlertType.UNDERCUT_THREAT, payload
    )

    message = await pubsub.get_message(timeout=1)
    assert message is not None
    assert message["channel"] == channel
    published = json.loads(message["data"])
    assert published == created[0]
    await pubsub.aclose()  # type: ignore[attr-defined]


@pytest.mark.unit
async def test_undercut_threat_no_subscribers_skips_alert(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    session_id = uuid.uuid4()
    leader_id = uuid.uuid4()
    trailing_id = uuid.uuid4()

    positions = [_fake_position(leader_id, 1), _fake_position(trailing_id, 2)]
    score_row = MagicMock(driver_id=trailing_id, undercut_score=0.75)

    mock_db_session.execute.side_effect = [
        _scalars_all_result(positions),
        _rows_result([score_row]),
        _rows_result([]),  # no subscribers
    ]

    dispatched = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert dispatched == []
    mock_db_session.add.assert_not_called()


@pytest.mark.unit
async def test_get_user_alerts_returns_ordered_list(mock_db_session: AsyncMock) -> None:
    user_id = uuid.uuid4()
    alerts = [_fake_alert(user_id, uuid.uuid4(), read_at=None)]
    mock_db_session.execute.return_value = _scalars_all_result(alerts)

    result = await alert_service.get_user_alerts(mock_db_session, user_id)

    assert len(result) == 1
    assert result[0].user_id == user_id


@pytest.mark.unit
async def test_get_user_alerts_filters_unread(mock_db_session: AsyncMock) -> None:
    user_id = uuid.uuid4()
    alerts = [_fake_alert(user_id, uuid.uuid4(), read_at=None)]
    mock_db_session.execute.return_value = _scalars_all_result(alerts)

    result = await alert_service.get_user_alerts(mock_db_session, user_id, unread=True)

    assert len(result) == 1
    assert result[0].read_at is None


@pytest.mark.unit
async def test_mark_alert_read_updates_and_returns(mock_db_session: AsyncMock) -> None:
    user_id = uuid.uuid4()
    alert = _fake_alert(user_id, uuid.uuid4(), read_at=None)
    mock_db_session.execute.return_value = _scalar_one_or_none_result(alert)

    result = await alert_service.mark_alert_read(mock_db_session, user_id, alert.id)

    assert alert.read_at is not None
    assert result.id == alert.id
    mock_db_session.commit.assert_awaited_once()


@pytest.mark.unit
async def test_mark_alert_read_raises_not_found(mock_db_session: AsyncMock) -> None:
    mock_db_session.execute.return_value = _scalar_one_or_none_result(None)

    with pytest.raises(NotFoundError):
        await alert_service.mark_alert_read(mock_db_session, uuid.uuid4(), uuid.uuid4())


@pytest.mark.unit
async def test_get_subscription_creates_default_when_missing(mock_db_session: AsyncMock) -> None:
    user_id = uuid.uuid4()
    mock_db_session.execute.return_value = _scalars_first_result(None)

    result = await alert_service.get_subscription(mock_db_session, user_id)

    assert result.user_id == user_id
    assert result.driver_ids == []
    mock_db_session.add.assert_called_once()
    mock_db_session.commit.assert_awaited_once()


@pytest.mark.unit
async def test_update_subscription_replaces_preferences(mock_db_session: AsyncMock) -> None:
    user_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    team_id = uuid.uuid4()
    existing = Subscription(
        id=uuid.uuid4(), user_id=user_id, driver_ids=[], team_ids=[], alert_types=[]
    )
    mock_db_session.execute.return_value = _scalars_first_result(existing)
    payload = SubscriptionCreate(
        driver_ids=[driver_id], team_ids=[team_id], alert_types=["UNDERCUT_THREAT"]
    )

    result = await alert_service.update_subscription(mock_db_session, user_id, payload)

    assert result.driver_ids == [driver_id]
    assert result.team_ids == [team_id]
    assert result.alert_types == ["UNDERCUT_THREAT"]
    mock_db_session.commit.assert_awaited_once()
