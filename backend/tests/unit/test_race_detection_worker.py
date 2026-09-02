"""Unit tests for workers/race_detection_worker.py — auto race detection logic.

fakeredis (sync FakeRedis, matching the task's own sync redis.Redis client)
stands in for the dedup key; Ergast, subprocess.Popen, and os.kill are mocked
so no network call, real process launch, or real signal happens. The last
three tests cover the Day 43 kill-switch: a real race launching must
force-stop any active demo replay first.
"""

import json
import signal
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import fakeredis as fakeredis_lib
import pandas as pd
import pytest

from backend.services.demo_service import DEMO_REPLAY_STATE_KEY
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
def test_kill_switch_force_stops_active_demo_replay() -> None:
    settings = MagicMock(auto_race_detection_enabled=True)
    race_start = datetime.now(UTC) - timedelta(minutes=5)
    mock_ergast_instance = MagicMock()
    mock_ergast_instance.get_race_schedule.return_value = _schedule_df(7, race_start)
    fake_redis = fakeredis_lib.FakeRedis(decode_responses=True)
    fake_redis.set(DEMO_REPLAY_STATE_KEY, json.dumps({"pid": 4242, "session_id": "abc"}))

    with (
        patch.object(race_detection_worker, "get_live_timing_settings", return_value=settings),
        patch("fastf1.ergast.Ergast", return_value=mock_ergast_instance),
        patch("redis.Redis.from_url", return_value=fake_redis),
        patch("backend.workers.race_detection_worker.os.kill") as mock_kill,
        patch("backend.workers.race_detection_worker.subprocess.Popen") as mock_popen,
    ):
        race_detection_worker.check_for_live_session()

    mock_kill.assert_called_once_with(4242, signal.SIGTERM)
    assert fake_redis.get(DEMO_REPLAY_STATE_KEY) is None
    mock_popen.assert_called_once()


@pytest.mark.unit
def test_kill_switch_tolerates_already_dead_replay_pid() -> None:
    settings = MagicMock(auto_race_detection_enabled=True)
    race_start = datetime.now(UTC) - timedelta(minutes=5)
    mock_ergast_instance = MagicMock()
    mock_ergast_instance.get_race_schedule.return_value = _schedule_df(7, race_start)
    fake_redis = fakeredis_lib.FakeRedis(decode_responses=True)
    fake_redis.set(DEMO_REPLAY_STATE_KEY, json.dumps({"pid": 9999}))

    with (
        patch.object(race_detection_worker, "get_live_timing_settings", return_value=settings),
        patch("fastf1.ergast.Ergast", return_value=mock_ergast_instance),
        patch("redis.Redis.from_url", return_value=fake_redis),
        patch("backend.workers.race_detection_worker.os.kill", side_effect=ProcessLookupError),
        patch("backend.workers.race_detection_worker.subprocess.Popen") as mock_popen,
    ):
        race_detection_worker.check_for_live_session()

    assert fake_redis.get(DEMO_REPLAY_STATE_KEY) is None
    mock_popen.assert_called_once()


@pytest.mark.unit
def test_launch_without_demo_replay_does_not_signal() -> None:
    settings = MagicMock(auto_race_detection_enabled=True)
    race_start = datetime.now(UTC) - timedelta(minutes=5)
    mock_ergast_instance = MagicMock()
    mock_ergast_instance.get_race_schedule.return_value = _schedule_df(7, race_start)
    fake_redis = fakeredis_lib.FakeRedis(decode_responses=True)

    with (
        patch.object(race_detection_worker, "get_live_timing_settings", return_value=settings),
        patch("fastf1.ergast.Ergast", return_value=mock_ergast_instance),
        patch("redis.Redis.from_url", return_value=fake_redis),
        patch("backend.workers.race_detection_worker.os.kill") as mock_kill,
        patch("backend.workers.race_detection_worker.subprocess.Popen") as mock_popen,
    ):
        race_detection_worker.check_for_live_session()

    mock_kill.assert_not_called()
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
