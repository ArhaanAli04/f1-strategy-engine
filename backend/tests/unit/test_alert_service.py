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
        _rows_result([]),  # driver_codes — message text not asserted here
        _rows_result([subscriber_row]),
    ]

    dispatched = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert len(dispatched) == 1
    assert dispatched[0]["driver_id"] == str(trailing_id)
    mock_db_session.add.assert_called_once()


@pytest.mark.unit
async def test_undercut_threat_message_uses_driver_codes_not_uuids(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    """The dispatched alert message reads like a timing screen (codes), not raw UUIDs."""
    session_id = uuid.uuid4()
    leader_id = uuid.uuid4()
    trailing_id = uuid.uuid4()
    subscriber_id = uuid.uuid4()

    positions = [_fake_position(leader_id, 1), _fake_position(trailing_id, 2)]
    score_row = MagicMock(driver_id=trailing_id, undercut_score=0.75)
    subscriber_row = MagicMock(user_id=subscriber_id)
    code_rows = [
        MagicMock(id=leader_id, code="RUS"),
        MagicMock(id=trailing_id, code="HUL"),
    ]

    mock_db_session.execute.side_effect = [
        _scalars_all_result(positions),
        _rows_result([score_row]),
        _rows_result(code_rows),
        _rows_result([subscriber_row]),
    ]

    dispatched = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert len(dispatched) == 1
    assert dispatched[0]["message"] == "Undercut threat: HUL on RUS (75%)"
    assert str(trailing_id) not in dispatched[0]["message"]
    assert str(leader_id) not in dispatched[0]["message"]


@pytest.mark.unit
async def test_undercut_threat_message_falls_back_to_uuid_when_code_missing(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    """A driver_id with no resolvable code degrades to the raw id, not a crash."""
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
        _rows_result([]),  # neither driver's code resolves
        _rows_result([subscriber_row]),
    ]

    dispatched = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert dispatched[0]["message"] == f"Undercut threat: {trailing_id} on {leader_id} (75%)"


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
        _rows_result([]),  # driver_codes
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
async def test_undercut_threat_second_call_deduped(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    """A second evaluate_threats call for the same pair within the dedup TTL fires nothing.

    Mirrors what actually happens live: prediction_worker calls evaluate_threats
    once per driver's StrategyPrediction commit, so the same trailing/ahead pair
    gets re-evaluated many times per lap round — only the first crossing within
    UNDERCUT_ALERT_DEDUP_TTL_SECONDS should dispatch.
    """
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
        _rows_result([]),  # driver_codes
        _rows_result([subscriber_row]),
    ]
    first = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)
    assert len(first) == 1

    mock_db_session.execute.side_effect = [
        _scalars_all_result(positions),
        _rows_result([score_row]),
        _rows_result([]),  # driver_codes
        _rows_result([subscriber_row]),
    ]
    second = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert second == []
    mock_db_session.add.assert_called_once()  # still just the first call's Alert row


@pytest.mark.unit
async def test_undercut_threat_different_pairing_not_deduped(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    """A different session re-firing the same trailing driver is a distinct dedup key."""
    session_id_a = uuid.uuid4()
    session_id_b = uuid.uuid4()
    leader_id = uuid.uuid4()
    trailing_id = uuid.uuid4()
    subscriber_id = uuid.uuid4()

    positions = [_fake_position(leader_id, 1), _fake_position(trailing_id, 2)]
    score_row = MagicMock(driver_id=trailing_id, undercut_score=0.75)
    subscriber_row = MagicMock(user_id=subscriber_id)

    mock_db_session.execute.side_effect = [
        _scalars_all_result(positions),
        _rows_result([score_row]),
        _rows_result([]),  # driver_codes
        _rows_result([subscriber_row]),
    ]
    first = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id_a)
    assert len(first) == 1

    mock_db_session.execute.side_effect = [
        _scalars_all_result(positions),
        _rows_result([score_row]),
        _rows_result([]),  # driver_codes
        _rows_result([subscriber_row]),
    ]
    second = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id_b)

    assert len(second) == 1
    assert mock_db_session.add.call_count == 2


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
        _rows_result([]),  # driver_codes
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
