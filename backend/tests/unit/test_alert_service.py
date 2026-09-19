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
from redis.exceptions import RedisError

from backend.core.exceptions import NotFoundError
from backend.models.telemetry import LapData
from backend.models.user import Alert, Subscription
from backend.schemas.alert_schema import AlertType
from backend.schemas.user_schema import SubscriptionCreate
from backend.services import alert_service


def _fake_position(
    driver_id: uuid.UUID, position: int, lap_number: int = 20, tyre_age_laps: int = 10
) -> MagicMock:
    lap = MagicMock(spec=LapData)
    lap.driver_id = driver_id
    lap.position = position
    lap.lap_number = lap_number
    lap.tyre_age_laps = tyre_age_laps
    return lap


def _context_result(row: Any) -> MagicMock:
    """_session_race_context's query: one row (season, round_number, total_laps) or None."""
    result = MagicMock()
    result.one_or_none.return_value = row
    return result


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
        _context_result(None),  # no race context -> DB-derived order, no lap gate
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
        _context_result(None),  # no race context -> DB-derived order, no lap gate
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
        _context_result(None),  # no race context -> DB-derived order, no lap gate
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
        _context_result(None),  # no race context -> DB-derived order, no lap gate
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
        _context_result(None),  # no race context -> DB-derived order, no lap gate
        _rows_result([]),  # driver_codes
        _rows_result([subscriber_row]),
    ]
    first = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)
    assert len(first) == 1

    mock_db_session.execute.side_effect = [
        _scalars_all_result(positions),
        _rows_result([score_row]),
        _context_result(None),  # no race context -> DB-derived order, no lap gate
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
        _context_result(None),  # no race context -> DB-derived order, no lap gate
        _rows_result([]),  # driver_codes
        _rows_result([subscriber_row]),
    ]
    first = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id_a)
    assert len(first) == 1

    mock_db_session.execute.side_effect = [
        _scalars_all_result(positions),
        _rows_result([score_row]),
        _context_result(None),  # no race context -> DB-derived order, no lap gate
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
        _context_result(None),  # no race context -> DB-derived order, no lap gate
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


# --- live standings + race-state gates
# (docs/live-race-ingestion-and-strategy-gaps-monza-2026.md Issue B) ---

_SEASON, _ROUND = 2026, 13


def _context_row(total_laps: int | None = 53) -> MagicMock:
    return MagicMock(season=_SEASON, round_number=_ROUND, total_laps=total_laps)


async def _set_live_order(
    client: fakeredis_lib.FakeAsyncRedis,
    session_id: uuid.UUID,
    ordered_ids: list[uuid.UUID],
    source: str = "live",
) -> None:
    entries = [
        {"driver_id": str(driver_id), "position": i} for i, driver_id in enumerate(ordered_ids, 1)
    ]
    await client.set(
        f"f1:{_SEASON}:{_ROUND}:gaps",
        json.dumps({"session_id": str(session_id), "source": source, "gaps": entries}),
    )


def _evaluate_effects(
    positions: list[Any],
    trailing_id: uuid.UUID,
    score: float,
    context: Any,
    codes: list[Any] | None = None,
    subscribers: bool = True,
) -> list[Any]:
    """The query order evaluate_threats runs in: positions, scores, race context,
    driver codes, then (only if it gets that far) the subscriber lookup."""
    effects = [
        _scalars_all_result(positions),
        _rows_result([MagicMock(driver_id=trailing_id, undercut_score=score)]),
        _context_result(context),
        _rows_result(codes or []),
    ]
    if subscribers:
        effects.append(_rows_result([MagicMock(user_id=uuid.uuid4())]))
    return effects


@pytest.mark.unit
async def test_live_standings_replace_a_retirees_stale_slot_in_the_pairing(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    """The stored rows still hold a retiree at P2 (its last lap-1 row), so the DB
    order would pair the trailing car with it — 'VER on LEC' on Monza 2026."""
    session_id, leader, retiree, trailing = (uuid.uuid4() for _ in range(4))
    positions = [
        _fake_position(leader, 1),
        _fake_position(retiree, 2, lap_number=1, tyre_age_laps=1),
        _fake_position(trailing, 3),
    ]
    await _set_live_order(fakeredis, session_id, [leader, trailing])  # retiree not in it
    codes = [
        MagicMock(id=leader, code="RUS"),
        MagicMock(id=retiree, code="LEC"),
        MagicMock(id=trailing, code="VER"),
    ]
    mock_db_session.execute.side_effect = _evaluate_effects(
        positions, trailing, 0.9, _context_row(), codes
    )

    dispatched = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert [d["message"] for d in dispatched] == ["Undercut threat: VER on RUS (90%)"]


@pytest.mark.unit
@pytest.mark.parametrize("case", ["replay", "other-session", "malformed"])
async def test_a_payload_that_is_not_this_sessions_live_standings_keeps_the_db_order(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis, case: str
) -> None:
    session_id, leader, retiree, trailing = (uuid.uuid4() for _ in range(4))
    positions = [
        _fake_position(leader, 1),
        _fake_position(retiree, 2),
        _fake_position(trailing, 3),
    ]
    if case == "replay":
        await _set_live_order(fakeredis, session_id, [leader, trailing], source="replay")
    elif case == "other-session":
        await _set_live_order(fakeredis, uuid.uuid4(), [leader, trailing])
    else:
        await fakeredis.set(f"f1:{_SEASON}:{_ROUND}:gaps", "not valid json")
    codes = [
        MagicMock(id=retiree, code="LEC"),
        MagicMock(id=trailing, code="VER"),
        MagicMock(id=leader, code="RUS"),
    ]
    mock_db_session.execute.side_effect = _evaluate_effects(
        positions, trailing, 0.9, _context_row(), codes
    )

    dispatched = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    # The DB order is unchanged behaviour: the trailing car is paired with the car at P2.
    assert [d["message"] for d in dispatched] == ["Undercut threat: VER on LEC (90%)"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tyre_age", "alerts"), [(1, False), (3, False), (4, True), (25, True)], ids=str
)
async def test_no_alert_for_a_driver_on_tyres_three_laps_old_or_less(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    tyre_age: int,
    alerts: bool,
) -> None:
    session_id, leader, trailing = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    positions = [_fake_position(leader, 1), _fake_position(trailing, 2, tyre_age_laps=tyre_age)]
    # A suppressed alert must not even reach the subscriber lookup: its query is
    # left out of the list, so reaching it would exhaust the mock and fail.
    mock_db_session.execute.side_effect = _evaluate_effects(
        positions, trailing, 0.9, _context_row(), subscribers=alerts
    )

    dispatched = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert bool(dispatched) is alerts


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lap", "total_laps", "alerts"),
    [
        (38, 53, True),  # 15 laps left: still alerts
        (39, 53, False),  # 14 left: suppressed
        (52, 53, False),
        (52, None, True),  # real distance unknown -> the limit is not applied
    ],
)
async def test_no_alert_with_fewer_than_fifteen_laps_left_and_only_when_distance_is_known(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    lap: int,
    total_laps: int | None,
    alerts: bool,
) -> None:
    session_id, leader, trailing = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    positions = [_fake_position(leader, 1), _fake_position(trailing, 2, lap_number=lap)]
    mock_db_session.execute.side_effect = _evaluate_effects(
        positions, trailing, 0.9, _context_row(total_laps), subscribers=alerts
    )

    dispatched = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert bool(dispatched) is alerts


@pytest.mark.unit
async def test_a_suppressed_alert_does_not_use_up_the_pairs_dedup_claim(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    session_id, leader, trailing = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fresh = [_fake_position(leader, 1), _fake_position(trailing, 2, tyre_age_laps=2)]
    mock_db_session.execute.side_effect = _evaluate_effects(
        fresh, trailing, 0.9, _context_row(), subscribers=False
    )
    assert await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id) == []

    aged = [_fake_position(leader, 1), _fake_position(trailing, 2, tyre_age_laps=6)]
    mock_db_session.execute.side_effect = _evaluate_effects(aged, trailing, 0.9, _context_row())
    second = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert len(second) == 1


@pytest.mark.unit
async def test_a_driver_with_no_stored_lap_row_is_not_suppressed(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    """In the live order but with no lap_data row yet: nothing to judge by, so no gate."""
    session_id, leader, trailing = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _set_live_order(fakeredis, session_id, [leader, trailing])
    mock_db_session.execute.side_effect = _evaluate_effects(
        [_fake_position(leader, 1)], trailing, 0.9, _context_row()
    )

    dispatched = await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert len(dispatched) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lap", "tyre_age", "total_laps", "expected"),
    [
        (20, 4, 53, False),
        (20, 3, 53, True),
        (38, 10, 53, False),
        (39, 10, 53, True),
        (39, 10, None, False),
        (1, 1, None, True),
    ],
)
def test_alert_suppressed_by_race_state_boundaries(
    lap: int, tyre_age: int, total_laps: int | None, expected: bool
) -> None:
    assert alert_service._alert_suppressed_by_race_state(lap, tyre_age, total_laps) is expected


@pytest.mark.unit
async def test_live_standing_order_sorts_by_position_not_payload_order(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id, a, b, c = (uuid.uuid4() for _ in range(4))
    entries = [
        {"driver_id": str(c), "position": 3},
        {"driver_id": str(a), "position": 1},
        {"driver_id": str(b), "position": 2},
    ]
    await fakeredis.set(
        f"f1:{_SEASON}:{_ROUND}:gaps",
        json.dumps({"session_id": str(session_id), "source": "live", "gaps": entries}),
    )

    order = await alert_service._live_standing_order(fakeredis, _SEASON, _ROUND, session_id)

    assert order == [a, b, c]


@pytest.mark.unit
async def test_live_standing_order_is_none_when_the_key_is_missing(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    order = await alert_service._live_standing_order(fakeredis, _SEASON, _ROUND, uuid.uuid4())

    assert order is None


@pytest.mark.unit
async def test_session_race_context_returns_season_round_and_real_total_laps(
    mock_db_session: AsyncMock,
) -> None:
    mock_db_session.execute.return_value = _context_result(_context_row(53))

    assert await alert_service._session_race_context(mock_db_session, uuid.uuid4()) == (
        _SEASON,
        _ROUND,
        53,
    )


@pytest.mark.unit
async def test_session_race_context_is_none_for_an_unknown_session(
    mock_db_session: AsyncMock,
) -> None:
    mock_db_session.execute.return_value = _context_result(None)

    assert await alert_service._session_race_context(mock_db_session, uuid.uuid4()) is None


@pytest.mark.unit
def test_property_alert_suppression_only_grows_as_tyres_get_fresher_or_the_race_nears_its_end() -> (
    None
):
    """Suppression is monotone: fresher tyres or a later lap can never re-enable an alert."""
    suppressed = alert_service._alert_suppressed_by_race_state
    for total_laps in (None, 44, 53, 78):
        for lap in range(1, 80):
            for tyre_age in range(0, 40):
                if suppressed(lap, tyre_age, total_laps):
                    if tyre_age > 0:
                        assert suppressed(lap, tyre_age - 1, total_laps)
                    assert suppressed(lap + 1, tyre_age, total_laps) or total_laps is None


@pytest.mark.unit
def test_property_with_an_unknown_race_distance_only_tyre_age_can_suppress() -> None:
    for lap in range(1, 200):
        for tyre_age in range(0, 40):
            expected = tyre_age < alert_service.UNDERCUT_ALERT_MIN_TYRE_AGE_LAPS
            assert alert_service._alert_suppressed_by_race_state(lap, tyre_age, None) is expected


# --- always-on pipeline counters (V5): alert order source, suppressions, dispatches ---


async def _alert_stats(client: fakeredis_lib.FakeAsyncRedis) -> dict[str, int]:
    raw = await client.hgetall(f"f1:{_SEASON}:{_ROUND}:pipeline_stats")
    return {k: int(v) for k, v in raw.items()}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lap", "tyre_age", "total_laps", "expected"),
    [
        (20, 10, 53, None),
        (20, 3, 53, "tyre_age"),
        (39, 10, 53, "laps_remaining"),
        (39, 3, 53, "tyre_age"),  # tyre age is checked first
        (39, 10, None, None),
    ],
)
def test_alert_suppression_reason_names_the_gate_that_fired(
    lap: int, tyre_age: int, total_laps: int | None, expected: str | None
) -> None:
    assert alert_service._alert_suppression_reason(lap, tyre_age, total_laps) == expected


@pytest.mark.unit
async def test_counters_record_live_order_and_a_dispatched_alert(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    session_id, leader, trailing = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _set_live_order(fakeredis, session_id, [leader, trailing])
    positions = [_fake_position(leader, 1), _fake_position(trailing, 2)]
    mock_db_session.execute.side_effect = _evaluate_effects(
        positions, trailing, 0.9, _context_row()
    )

    await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert await _alert_stats(fakeredis) == {
        "alert_order_source_live": 1,
        "alerts_dispatched": 1,
    }


@pytest.mark.unit
async def test_counters_record_db_order_when_there_is_no_live_payload(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    session_id, leader, trailing = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    positions = [_fake_position(leader, 1), _fake_position(trailing, 2, tyre_age_laps=1)]
    mock_db_session.execute.side_effect = _evaluate_effects(
        positions, trailing, 0.9, _context_row(), subscribers=False
    )

    await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert await _alert_stats(fakeredis) == {
        "alert_order_source_db": 1,
        "alerts_suppressed_tyre_age": 1,
    }


@pytest.mark.unit
async def test_counters_name_the_laps_remaining_gate_separately(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    session_id, leader, trailing = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    positions = [_fake_position(leader, 1), _fake_position(trailing, 2, lap_number=45)]
    mock_db_session.execute.side_effect = _evaluate_effects(
        positions, trailing, 0.9, _context_row(53), subscribers=False
    )

    await alert_service.evaluate_threats(mock_db_session, fakeredis, session_id)

    assert (await _alert_stats(fakeredis))["alerts_suppressed_laps_remaining"] == 1


@pytest.mark.unit
async def test_a_redis_failure_while_counting_never_breaks_alert_evaluation(
    mock_db_session: AsyncMock,
) -> None:
    broken = AsyncMock()
    broken.hincrby.side_effect = RedisError("down")

    await alert_service._bump_pipeline_stat(
        broken, _SEASON, _ROUND, "alerts_dispatched"
    )  # no raise
