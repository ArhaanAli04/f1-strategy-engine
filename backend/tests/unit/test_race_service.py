"""Unit tests for services/race_service.py.

_stub_cache_lock patches cache_lock in both cache_service (where @cacheable's
internal single-flight lock lives — used by get_races/get_race) and race_service
(get_current_race's hand-rolled cache-aside calls its own imported cache_lock
reference directly, a separate name binding from cache_service.cache_lock) — same
no-op pattern test_strategy_service.py established, fakeredis has no Lua/EVALSHA
support needed by redis-py's real Lock.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis as fakeredis_lib
import pandas as pd
import pytest
from redis.exceptions import LockNotOwnedError

from backend.core.exceptions import NotFoundError
from backend.schemas.race_schema import RaceResponse
from backend.services import cache_service, race_service


class _NoOpLock:
    async def acquire(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def release(self) -> None:
        return None


class _AcquireFailsLock:
    async def acquire(self, *args: Any, **kwargs: Any) -> bool:
        return False

    async def release(self) -> None:
        return None


class _ReleaseRaisesLock:
    async def acquire(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def release(self) -> None:
        raise LockNotOwnedError("lock expired before release")


@pytest.fixture(autouse=True)
def _stub_cache_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_service, "cache_lock", lambda client, key: _NoOpLock())
    monkeypatch.setattr(race_service, "cache_lock", lambda client, key: _NoOpLock())


def _scalar_one_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def _scalar_one_or_none_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_all_result(items: list[Any]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


class _FakeCircuit:
    id = uuid.uuid4()
    name = "Test Circuit"
    country = "Testland"
    track_length_km = 5.0
    lap_record_seconds = 90.0
    created_at = datetime.now(UTC)


def _fake_race(race_id: uuid.UUID) -> Any:
    class _FakeRace:
        id = race_id
        season = 2026
        round_number = 10
        circuit_id = _FakeCircuit.id
        race_date = date(2026, 7, 20)
        weather = None
        status = "scheduled"
        circuit = _FakeCircuit()
        sessions: list[Any] = []

    return _FakeRace()


def _fake_session(session_id: uuid.UUID, race_id: uuid.UUID) -> Any:
    parent_race_id = race_id

    class _FakeSession:
        id = session_id
        race_id = parent_race_id
        session_type = "R"
        session_date = date(2026, 7, 20)

    return _FakeSession()


def _sentinel_race_response() -> RaceResponse:
    return RaceResponse(
        id=uuid.uuid4(),
        season=2026,
        round_number=9,
        circuit_id=uuid.uuid4(),
        race_date=date(2026, 7, 1),
        weather=None,
        status="scheduled",
        circuit=None,
        sessions=[],
    )


@pytest.mark.unit
async def test_get_races_returns_paginated_list(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    races = [_fake_race(uuid.uuid4()), _fake_race(uuid.uuid4())]
    mock_db_session.execute.side_effect = [
        _scalar_one_result(5),
        _scalars_all_result(races),
    ]

    result = await race_service.get_races(fakeredis, mock_db_session, page=1, page_size=2)

    assert result.page_size == 2
    assert result.total == 5
    assert len(result.items) == 2


@pytest.mark.unit
async def test_get_race_raises_not_found_for_unknown_id(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    mock_db_session.execute.return_value = _scalar_one_or_none_result(None)

    with pytest.raises(NotFoundError):
        await race_service.get_race(fakeredis, mock_db_session, uuid.uuid4())


@pytest.mark.unit
async def test_get_current_race_caches_negative_result(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_mock = AsyncMock(side_effect=NotFoundError("no current race ingested"))
    monkeypatch.setattr(race_service, "_fetch_current_race", fetch_mock)

    with pytest.raises(NotFoundError):
        await race_service.get_current_race(fakeredis, mock_db_session)
    assert fetch_mock.call_count == 1

    with pytest.raises(NotFoundError):
        await race_service.get_current_race(fakeredis, mock_db_session)
    assert fetch_mock.call_count == 1  # second call hit the cached negative result

    key = race_service._key_current_race(fakeredis, mock_db_session)
    ttl = await fakeredis.ttl(key)
    assert 0 < ttl <= race_service.CURRENT_RACE_NOT_FOUND_TTL_SECONDS


@pytest.mark.unit
async def test_cache_hit_skips_db_query(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    race_id = uuid.uuid4()
    cached_race = {
        "id": str(race_id),
        "season": 2026,
        "round_number": 10,
        "circuit_id": str(uuid.uuid4()),
        "race_date": "2026-07-20",
        "weather": None,
        "status": "scheduled",
        "circuit": None,
        "sessions": [],
    }
    key = race_service._key_race(fakeredis, mock_db_session, race_id)
    await cache_service.cache_set(fakeredis, key, cached_race, ttl=86400)

    result = await race_service.get_race(fakeredis, mock_db_session, race_id)

    assert result.id == race_id
    mock_db_session.execute.assert_not_called()


@pytest.mark.unit
async def test_get_races_filters_by_season_and_round(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    races = [_fake_race(uuid.uuid4())]
    mock_db_session.execute.side_effect = [_scalar_one_result(1), _scalars_all_result(races)]

    result = await race_service.get_races(
        fakeredis, mock_db_session, season=2026, round_number=10, page=1, page_size=20
    )

    assert result.total == 1
    assert len(result.items) == 1


@pytest.mark.unit
async def test_get_race_returns_race_with_circuit_and_sessions(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    race = _fake_race(uuid.uuid4())
    mock_db_session.execute.return_value = _scalar_one_or_none_result(race)

    result = await race_service.get_race(fakeredis, mock_db_session, race.id)

    assert result.id == race.id
    assert result.circuit is not None
    assert result.circuit.name == "Test Circuit"


@pytest.mark.unit
async def test_get_session_returns_session(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    race_id = uuid.uuid4()
    session_id = uuid.uuid4()
    mock_db_session.execute.return_value = _scalar_one_or_none_result(
        _fake_session(session_id, race_id)
    )

    result = await race_service.get_session(fakeredis, mock_db_session, race_id, session_id)

    assert result.id == session_id
    assert result.session_type == "R"


@pytest.mark.unit
async def test_get_session_raises_not_found(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    mock_db_session.execute.return_value = _scalar_one_or_none_result(None)

    with pytest.raises(NotFoundError):
        await race_service.get_session(fakeredis, mock_db_session, uuid.uuid4(), uuid.uuid4())


@pytest.mark.unit
async def test_fetch_current_race_returns_race_when_ingested(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    season = datetime.now(UTC).year
    future_date = datetime.now(UTC) + timedelta(days=10)
    schedule = pd.DataFrame({"round": [5], "raceDate": [future_date]})
    race = _fake_race(uuid.uuid4())
    race.season = season
    race.round_number = 5
    mock_db_session.execute.return_value = _scalar_one_or_none_result(race)

    with patch("fastf1.ergast.Ergast") as mock_ergast_cls:
        mock_ergast_cls.return_value.get_race_schedule.return_value = schedule
        result = await race_service._fetch_current_race(fakeredis, mock_db_session)

    assert result["round_number"] == 5


@pytest.mark.unit
async def test_fetch_current_race_raises_when_schedule_empty(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    with patch("fastf1.ergast.Ergast") as mock_ergast_cls:
        mock_ergast_cls.return_value.get_race_schedule.return_value = pd.DataFrame()

        with pytest.raises(NotFoundError):
            await race_service._fetch_current_race(fakeredis, mock_db_session)


@pytest.mark.unit
async def test_fetch_current_race_raises_when_not_ingested(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    future_date = datetime.now(UTC) + timedelta(days=10)
    schedule = pd.DataFrame({"round": [5], "raceDate": [future_date]})
    mock_db_session.execute.return_value = _scalar_one_or_none_result(None)

    with patch("fastf1.ergast.Ergast") as mock_ergast_cls:
        mock_ergast_cls.return_value.get_race_schedule.return_value = schedule

        with pytest.raises(NotFoundError):
            await race_service._fetch_current_race(fakeredis, mock_db_session)


@pytest.mark.unit
async def test_get_current_race_returns_cached_race(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    cached_race = {
        "id": str(uuid.uuid4()),
        "season": 2026,
        "round_number": 12,
        "circuit_id": str(uuid.uuid4()),
        "race_date": "2026-08-01",
        "weather": None,
        "status": "scheduled",
        "circuit": None,
        "sessions": [],
    }
    key = race_service._key_current_race(fakeredis, mock_db_session)
    await cache_service.cache_set(fakeredis, key, cached_race, ttl=300)

    result = await race_service.get_current_race(fakeredis, mock_db_session)

    assert result.round_number == 12
    mock_db_session.execute.assert_not_called()


@pytest.mark.unit
async def test_get_current_race_lock_not_acquired_finds_cache_populated_meanwhile(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(race_service, "cache_lock", lambda client, key: _AcquireFailsLock())
    sentinel = _sentinel_race_response()
    read_cache_mock = AsyncMock(side_effect=[None, sentinel])
    monkeypatch.setattr(race_service, "_read_current_race_cache", read_cache_mock)
    fetch_and_cache_mock = AsyncMock()
    monkeypatch.setattr(race_service, "_fetch_and_cache_current_race", fetch_and_cache_mock)

    result = await race_service.get_current_race(fakeredis, mock_db_session)

    assert result == sentinel
    assert read_cache_mock.await_count == 2
    fetch_and_cache_mock.assert_not_called()


@pytest.mark.unit
async def test_get_current_race_lock_not_acquired_falls_back_to_compute(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(race_service, "cache_lock", lambda client, key: _AcquireFailsLock())
    read_cache_mock = AsyncMock(side_effect=[None, None])
    monkeypatch.setattr(race_service, "_read_current_race_cache", read_cache_mock)
    sentinel = _sentinel_race_response()
    fetch_and_cache_mock = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(race_service, "_fetch_and_cache_current_race", fetch_and_cache_mock)

    result = await race_service.get_current_race(fakeredis, mock_db_session)

    assert result == sentinel
    fetch_and_cache_mock.assert_awaited_once()


@pytest.mark.unit
async def test_get_current_race_acquired_lock_finds_cache_populated_between_checks(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = _sentinel_race_response()
    read_cache_mock = AsyncMock(side_effect=[None, sentinel])
    monkeypatch.setattr(race_service, "_read_current_race_cache", read_cache_mock)

    result = await race_service.get_current_race(fakeredis, mock_db_session)

    assert result == sentinel


@pytest.mark.unit
async def test_get_current_race_lock_release_swallows_lock_not_owned_error(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(race_service, "cache_lock", lambda client, key: _ReleaseRaisesLock())
    read_cache_mock = AsyncMock(side_effect=[None, None])
    monkeypatch.setattr(race_service, "_read_current_race_cache", read_cache_mock)
    sentinel = _sentinel_race_response()
    fetch_and_cache_mock = AsyncMock(return_value=sentinel)
    monkeypatch.setattr(race_service, "_fetch_and_cache_current_race", fetch_and_cache_mock)

    result = await race_service.get_current_race(fakeredis, mock_db_session)

    assert result == sentinel


def _fake_upcoming_session(session_type: str, scheduled_start: datetime | None) -> Any:
    class _FakeSession:
        pass

    session = _FakeSession()
    session.session_type = session_type  # type: ignore[attr-defined]
    session.scheduled_start = scheduled_start  # type: ignore[attr-defined]
    return session


@pytest.mark.unit
def test_race_session_scheduled_start_finds_matching_type() -> None:
    race = _fake_race(uuid.uuid4())
    race_start = datetime(2026, 8, 23, 13, 0, tzinfo=UTC)
    race.sessions = [
        _fake_upcoming_session("FP1", datetime(2026, 8, 21, 10, 30, tzinfo=UTC)),
        _fake_upcoming_session("R", race_start),
    ]

    assert race_service._race_session_scheduled_start(race, "R") == race_start
    assert race_service._race_session_scheduled_start(race, "Q") is None


@pytest.mark.unit
def test_to_upcoming_race_response_maps_race_fields() -> None:
    race = _fake_race(uuid.uuid4())
    race.event_name = "Dutch Grand Prix"
    race.season = 2026
    race.round_number = 12
    race_start = datetime(2026, 8, 23, 13, 0, tzinfo=UTC)
    race.sessions = [_fake_upcoming_session("R", race_start)]

    data = race_service._to_upcoming_race_response(race)

    assert data["race_name"] == "Dutch Grand Prix"
    assert data["round_number"] == 12
    assert data["circuit_id"] == str(race.circuit_id)
    assert data["scheduled_start"] == "2026-08-23T13:00:00Z"


@pytest.mark.unit
async def test_fetch_upcoming_race_from_db_returns_race_when_found(
    mock_db_session: AsyncMock,
) -> None:
    race = _fake_race(uuid.uuid4())
    mock_db_session.execute.return_value = _scalar_one_or_none_result(race)

    result = await race_service._fetch_upcoming_race_from_db(
        mock_db_session, 2026, date(2026, 8, 1)
    )

    assert result is race


@pytest.mark.unit
async def test_fetch_upcoming_race_from_db_returns_none_when_nothing_found(
    mock_db_session: AsyncMock,
) -> None:
    mock_db_session.execute.return_value = _scalar_one_or_none_result(None)

    result = await race_service._fetch_upcoming_race_from_db(
        mock_db_session, 2026, date(2026, 8, 1)
    )

    assert result is None


@pytest.mark.unit
async def test_fetch_upcoming_race_from_fastf1_raises_when_schedule_has_no_future_events(
    mock_db_session: AsyncMock,
) -> None:
    past_event = pd.DataFrame(
        {
            "RoundNumber": [1],
            "EventName": ["Old Grand Prix"],
            "Location": ["Somewhere"],
            "EventDate": [pd.Timestamp("2026-01-01", tz="UTC")],
        }
    )

    with patch("fastf1.get_event_schedule", return_value=past_event):
        with pytest.raises(NotFoundError):
            await race_service._fetch_upcoming_race_from_fastf1(
                mock_db_session, 2026, date(2026, 8, 1)
            )


@pytest.mark.unit
async def test_fetch_upcoming_race_from_fastf1_raises_when_circuit_unresolvable(
    mock_db_session: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.scripts._ingest_common import RoundSkippedError

    future_event = pd.DataFrame(
        {
            "RoundNumber": [15],
            "EventName": ["Made Up Grand Prix"],
            "Location": ["Nowhereville"],
            "EventDate": [pd.Timestamp("2026-09-01", tz="UTC")],
        }
    )
    circuit_mock = AsyncMock(side_effect=RoundSkippedError("no circuit mapping"))
    monkeypatch.setattr(race_service, "get_or_create_circuit", circuit_mock)

    with patch("fastf1.get_event_schedule", return_value=future_event):
        with pytest.raises(NotFoundError):
            await race_service._fetch_upcoming_race_from_fastf1(
                mock_db_session, 2026, date(2026, 8, 1)
            )


@pytest.mark.unit
async def test_get_upcoming_race_returns_cached_race(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    cached_race = {
        "id": str(uuid.uuid4()),
        "season": 2026,
        "round_number": 12,
        "race_name": "Dutch Grand Prix",
        "circuit_id": str(uuid.uuid4()),
        "race_date": "2026-08-23",
        "scheduled_start": "2026-08-23T13:00:00Z",
    }
    key = race_service._key_upcoming_race()
    await cache_service.cache_set(fakeredis, key, cached_race, ttl=300)

    result = await race_service.get_upcoming_race(fakeredis, mock_db_session)

    assert result.round_number == 12
    mock_db_session.execute.assert_not_called()


@pytest.mark.unit
async def test_get_upcoming_race_caches_negative_result(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_fetch_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(race_service, "_fetch_upcoming_race_from_db", db_fetch_mock)
    fastf1_fetch_mock = AsyncMock(side_effect=NotFoundError("no upcoming race for season 2026"))
    monkeypatch.setattr(race_service, "_fetch_upcoming_race_from_fastf1", fastf1_fetch_mock)

    with pytest.raises(NotFoundError):
        await race_service.get_upcoming_race(fakeredis, mock_db_session)
    assert fastf1_fetch_mock.call_count == 1

    with pytest.raises(NotFoundError):
        await race_service.get_upcoming_race(fakeredis, mock_db_session)
    assert fastf1_fetch_mock.call_count == 1  # second call hit the cached negative result

    key = race_service._key_upcoming_race()
    ttl = await fakeredis.ttl(key)
    assert 0 < ttl <= race_service.UPCOMING_RACE_NOT_FOUND_TTL_SECONDS
