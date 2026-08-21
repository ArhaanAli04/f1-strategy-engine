"""Unit tests for workers/race_detection_worker.py — auto race detection logic.

fakeredis (sync FakeRedis, matching the task's own sync redis.Redis client)
stands in for the dedup key; Ergast and subprocess.Popen are mocked so no
network call or real process launch happens.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import fakeredis as fakeredis_lib
import pandas as pd
import pytest

from backend.workers import race_detection_worker


def _schedule_df(round_number: int, race_start: datetime) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "round": round_number,
                "raceDate": race_start.date(),
                "raceTime": race_start.time(),
            }
        ]
    )


def _empty_schedule_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["round", "raceDate", "raceTime"])


@pytest.mark.unit
def test_disabled_skips_ergast_and_launch() -> None:
    settings = MagicMock(auto_race_detection_enabled=False)
    with (
        patch.object(race_detection_worker, "get_live_timing_settings", return_value=settings),
        patch("fastf1.ergast.Ergast") as mock_ergast,
        patch("backend.workers.race_detection_worker.subprocess.Popen") as mock_popen,
    ):
        race_detection_worker.check_for_live_session()

    mock_ergast.assert_not_called()
    mock_popen.assert_not_called()


@pytest.mark.unit
def test_no_upcoming_race_is_noop() -> None:
    settings = MagicMock(auto_race_detection_enabled=True)
    mock_ergast_instance = MagicMock()
    mock_ergast_instance.get_race_schedule.return_value = _empty_schedule_df()

    with (
        patch.object(race_detection_worker, "get_live_timing_settings", return_value=settings),
        patch("fastf1.ergast.Ergast", return_value=mock_ergast_instance),
        patch("backend.workers.race_detection_worker.subprocess.Popen") as mock_popen,
    ):
        race_detection_worker.check_for_live_session()

    mock_popen.assert_not_called()


@pytest.mark.unit
def test_race_outside_grace_window_is_noop() -> None:
    """A race that started well over 30 minutes ago should not trigger."""
    settings = MagicMock(auto_race_detection_enabled=True)
    stale_start = datetime.now(UTC) - timedelta(hours=2)
    mock_ergast_instance = MagicMock()
    mock_ergast_instance.get_race_schedule.return_value = _schedule_df(1, stale_start)

    with (
        patch.object(race_detection_worker, "get_live_timing_settings", return_value=settings),
        patch("fastf1.ergast.Ergast", return_value=mock_ergast_instance),
        patch("backend.workers.race_detection_worker.subprocess.Popen") as mock_popen,
    ):
        race_detection_worker.check_for_live_session()

    mock_popen.assert_not_called()


@pytest.mark.unit
def test_race_within_grace_window_triggers_launch() -> None:
    settings = MagicMock(auto_race_detection_enabled=True)
    race_start = datetime.now(UTC) - timedelta(minutes=5)
    mock_ergast_instance = MagicMock()
    mock_ergast_instance.get_race_schedule.return_value = _schedule_df(7, race_start)
    fake_redis = fakeredis_lib.FakeRedis(decode_responses=True)

    with (
        patch.object(race_detection_worker, "get_live_timing_settings", return_value=settings),
        patch("fastf1.ergast.Ergast", return_value=mock_ergast_instance),
        patch("redis.Redis.from_url", return_value=fake_redis),
        patch("backend.workers.race_detection_worker.subprocess.Popen") as mock_popen,
    ):
        race_detection_worker.check_for_live_session()

    mock_popen.assert_called_once()
    argv = mock_popen.call_args.args[0]
    assert "--round" in argv
    assert str(7) in argv
    assert "--session-type" in argv
    assert "R" in argv


@pytest.mark.unit
def test_already_triggered_skips_second_launch() -> None:
    """Simulates two consecutive 5-minute polls landing on the same race."""
    settings = MagicMock(auto_race_detection_enabled=True)
    race_start = datetime.now(UTC) - timedelta(minutes=5)
    mock_ergast_instance = MagicMock()
    mock_ergast_instance.get_race_schedule.return_value = _schedule_df(7, race_start)
    fake_redis = fakeredis_lib.FakeRedis(decode_responses=True)

    with (
        patch.object(race_detection_worker, "get_live_timing_settings", return_value=settings),
        patch("fastf1.ergast.Ergast", return_value=mock_ergast_instance),
        patch("redis.Redis.from_url", return_value=fake_redis),
        patch("backend.workers.race_detection_worker.subprocess.Popen") as mock_popen,
    ):
        race_detection_worker.check_for_live_session()
        race_detection_worker.check_for_live_session()

    mock_popen.assert_called_once()


@pytest.mark.unit
def test_ergast_down_does_not_raise() -> None:
    settings = MagicMock(auto_race_detection_enabled=True)
    mock_ergast_instance = MagicMock()
    mock_ergast_instance.get_race_schedule.side_effect = ConnectionError("Ergast unreachable")

    with (
        patch.object(race_detection_worker, "get_live_timing_settings", return_value=settings),
        patch("fastf1.ergast.Ergast", return_value=mock_ergast_instance),
        patch("backend.workers.race_detection_worker.subprocess.Popen") as mock_popen,
    ):
        race_detection_worker.check_for_live_session()  # must not raise

    mock_popen.assert_not_called()
