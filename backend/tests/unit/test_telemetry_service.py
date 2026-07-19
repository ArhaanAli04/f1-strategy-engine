"""Unit tests for services/telemetry_service.py — live reads, normalization, session gaps.

_stub_cache_lock stubs cache_service.cache_lock — @cacheable's internal
single-flight lock lives in cache_service.py, so patching it there covers the
@cacheable-decorated get_lap_history/get_session_gaps below. Same no-op pattern
test_strategy_service.py established Day 14: fakeredis has no Lua/EVALSHA
support, which redis-py's real Lock needs to release().
"""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis as fakeredis_lib
import numpy as np
import pandas as pd
import pytest

from backend.core.exceptions import NotFoundError, TelemetryNotAvailableError
from backend.services import cache_service, telemetry_service


class _NoOpLock:
    async def acquire(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def release(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_cache_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_service, "cache_lock", lambda client, key: _NoOpLock())


def _rows_result(rows: list[Any]) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    return result


@pytest.mark.unit
async def test_get_live_lap_reads_from_redis(fakeredis: fakeredis_lib.FakeAsyncRedis) -> None:
    season, round_number = 2026, 10
    driver_id = uuid.uuid4()
    car_number_key = f"f1:{season}:{round_number}:driver:{driver_id}:car_number"
    car_latest_key = f"f1:{season}:{round_number}:car:44:latest"

    await cache_service.cache_set(fakeredis, car_number_key, 44)
    await cache_service.cache_set(fakeredis, car_latest_key, {"Channels": {"2": 300}})

    result = await telemetry_service.get_live_lap(fakeredis, season, round_number, driver_id)

    assert result == {"Channels": {"2": 300}}


@pytest.mark.unit
def test_normalize_telemetry_converts_timedelta() -> None:
    raw = {"lap_time": pd.Timedelta(seconds=91.234), "speed": 300}

    normalized = telemetry_service.normalize_telemetry(raw)

    assert normalized["lap_time"] == pytest.approx(91.234)
    assert isinstance(normalized["lap_time"], float)
    assert normalized["speed"] == 300


@pytest.mark.unit
async def test_session_gaps_returns_all_20_drivers(mock_db_session: AsyncMock) -> None:
    session_id = uuid.uuid4()
    driver_ids = [uuid.uuid4() for _ in range(20)]
    rows = [
        {
            "driver_id": driver_id,
            "lap_number": 20,
            "position": i + 1,
            "cumulative_seconds": 1800.0 + i * 0.5,
        }
        for i, driver_id in enumerate(driver_ids)
    ]
    mock_db_session.execute.return_value = _rows_result(rows)

    result = await telemetry_service._compute_session_gaps(mock_db_session, session_id)

    assert len(result["gaps"]) == 20
    assert {gap["driver_id"] for gap in result["gaps"]} == {str(d) for d in driver_ids}


@pytest.mark.unit
def test_normalize_telemetry_handles_all_value_types() -> None:
    ts = pd.Timestamp("2026-07-20T12:00:00Z")
    raw = {
        "none_val": None,
        "nested": {"inner": pd.Timedelta(seconds=2.0)},
        "list_val": [1, pd.Timedelta(seconds=0.5)],
        "tuple_val": (2, 3),
        "timestamp": ts,
        "np_int": np.int64(7),
        "np_nan": np.float64("nan"),
        "float_nan": float("nan"),
        "plain": 42,
    }

    normalized = telemetry_service.normalize_telemetry(raw)

    assert normalized["none_val"] is None
    assert normalized["nested"]["inner"] == 2.0
    assert normalized["list_val"] == [1, 0.5]
    assert normalized["tuple_val"] == [2, 3]
    assert normalized["timestamp"] == ts.isoformat()
    assert normalized["np_int"] == 7
    assert normalized["np_nan"] is None
    assert normalized["float_nan"] is None
    assert normalized["plain"] == 42


@pytest.mark.unit
def test_decode_drs_status_handles_none_known_and_unknown_codes() -> None:
    assert telemetry_service._decode_drs_status(None) is None
    assert telemetry_service._decode_drs_status(8) == "available"
    assert telemetry_service._decode_drs_status(99) == "unknown"


@pytest.mark.unit
def test_decode_car_channels_maps_known_fields() -> None:
    entry = {"Channels": {"2": 300, "3": 6, "4": 80, "5": 1, "45": 10}}

    decoded = telemetry_service._decode_car_channels(entry)

    assert decoded == {
        "speed_kmh": 300.0,
        "throttle_pct": 80.0,
        "brake": True,
        "gear": 6,
        "drs": "enabled",
    }


@pytest.mark.unit
def test_decode_car_channels_returns_none_defaults_when_channels_missing() -> None:
    expected = dict.fromkeys(telemetry_service._CAR_DATA_CHANNELS.values())

    assert telemetry_service._decode_car_channels({}) == expected
    assert telemetry_service._decode_car_channels({"Channels": "not-a-dict"}) == expected


@pytest.mark.unit
async def test_resolve_season_round_returns_season_and_round(mock_db_session: AsyncMock) -> None:
    row_result = MagicMock()
    row_result.one_or_none.return_value = (2026, 12)
    mock_db_session.execute.return_value = row_result

    season, round_number = await telemetry_service.resolve_season_round(
        mock_db_session, uuid.uuid4()
    )

    assert (season, round_number) == (2026, 12)


@pytest.mark.unit
async def test_resolve_season_round_raises_not_found(mock_db_session: AsyncMock) -> None:
    row_result = MagicMock()
    row_result.one_or_none.return_value = None
    mock_db_session.execute.return_value = row_result

    with pytest.raises(NotFoundError):
        await telemetry_service.resolve_season_round(mock_db_session, uuid.uuid4())


@pytest.mark.unit
async def test_get_live_lap_raises_when_no_car_number_mapped(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    with pytest.raises(TelemetryNotAvailableError):
        await telemetry_service.get_live_lap(fakeredis, 2026, 10, uuid.uuid4())


@pytest.mark.unit
async def test_get_live_lap_raises_when_no_live_sample_cached(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    driver_id = uuid.uuid4()
    car_number_key = f"f1:2026:10:driver:{driver_id}:car_number"
    await cache_service.cache_set(fakeredis, car_number_key, 44)

    with pytest.raises(TelemetryNotAvailableError):
        await telemetry_service.get_live_lap(fakeredis, 2026, 10, driver_id)


@pytest.mark.unit
async def test_get_live_car_channels_returns_decoded_when_available(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    driver_id = uuid.uuid4()
    car_number_key = f"f1:2026:10:driver:{driver_id}:car_number"
    car_latest_key = "f1:2026:10:car:44:latest"
    await cache_service.cache_set(fakeredis, car_number_key, 44)
    await cache_service.cache_set(fakeredis, car_latest_key, {"Channels": {"2": 250}})

    result = await telemetry_service.get_live_car_channels(fakeredis, 2026, 10, driver_id)

    assert result["speed_kmh"] == 250.0


@pytest.mark.unit
async def test_get_live_car_channels_returns_none_defaults_when_car_number_missing(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    result = await telemetry_service.get_live_car_channels(fakeredis, 2026, 10, uuid.uuid4())

    assert all(value is None for value in result.values())


@pytest.mark.unit
async def test_get_live_car_channels_returns_none_defaults_when_sample_missing(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    driver_id = uuid.uuid4()
    car_number_key = f"f1:2026:10:driver:{driver_id}:car_number"
    await cache_service.cache_set(fakeredis, car_number_key, 44)

    result = await telemetry_service.get_live_car_channels(fakeredis, 2026, 10, driver_id)

    assert all(value is None for value in result.values())


@pytest.mark.unit
async def test_get_lap_history_returns_time_bucketed_rows(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    bucket_time = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
    rows = [
        {
            "bucket": bucket_time,
            "avg_sector1_seconds": 28.0,
            "avg_sector2_seconds": 34.0,
            "avg_sector3_seconds": 27.5,
            "avg_lap_time_seconds": 89.5,
            "lap_count": 5,
        }
    ]
    mock_db_session.execute.return_value = _rows_result(rows)

    result = await telemetry_service.get_lap_history(
        fakeredis, mock_db_session, 2026, 10, session_id, driver_id, last_n=5
    )

    assert len(result) == 1
    assert result[0]["lap_count"] == 5
    assert result[0]["bucket"] == bucket_time.isoformat()


@pytest.mark.unit
async def test_get_session_gaps_returns_gaps(
    mock_db_session: AsyncMock, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    rows = [{"driver_id": driver_id, "lap_number": 10, "position": 1, "cumulative_seconds": 900.0}]
    mock_db_session.execute.return_value = _rows_result(rows)

    result = await telemetry_service.get_session_gaps(
        fakeredis, mock_db_session, 2026, 10, session_id
    )

    assert result["session_id"] == str(session_id)
    assert len(result["gaps"]) == 1


@pytest.mark.unit
async def test_session_scoped_wrappers_resolve_season_round_then_delegate(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    season, round_number = 2026, 10

    async def _fake_resolve(db: Any, sid: uuid.UUID) -> tuple[int, int]:
        assert sid == session_id
        return season, round_number

    monkeypatch.setattr(telemetry_service, "resolve_season_round", _fake_resolve)

    live_lap_mock = AsyncMock(return_value={"speed_kmh": 300})
    monkeypatch.setattr(telemetry_service, "get_live_lap", live_lap_mock)
    live_result = await telemetry_service.get_live_lap_for_session(
        fakeredis, mock_db_session, session_id, driver_id
    )
    live_lap_mock.assert_awaited_once_with(fakeredis, season, round_number, driver_id)
    assert live_result.data == {"speed_kmh": 300}

    history_mock = AsyncMock(
        return_value=[
            {
                "bucket": "2026-07-20T14:00:00+00:00",
                "avg_sector1_seconds": 28.0,
                "avg_sector2_seconds": 34.0,
                "avg_sector3_seconds": 27.5,
                "avg_lap_time_seconds": 89.5,
                "lap_count": 5,
            }
        ]
    )
    monkeypatch.setattr(telemetry_service, "get_lap_history", history_mock)
    history_result = await telemetry_service.get_lap_history_for_session(
        fakeredis, mock_db_session, session_id, driver_id, last_n=5
    )
    history_mock.assert_awaited_once_with(
        fakeredis, mock_db_session, season, round_number, session_id, driver_id, 5
    )
    assert len(history_result) == 1

    gaps_mock = AsyncMock(return_value={"session_id": str(session_id), "gaps": []})
    monkeypatch.setattr(telemetry_service, "get_session_gaps", gaps_mock)
    gaps_result = await telemetry_service.get_session_gaps_for_session(
        fakeredis, mock_db_session, session_id
    )
    gaps_mock.assert_awaited_once_with(fakeredis, mock_db_session, season, round_number, session_id)
    assert gaps_result.session_id == session_id
