"""Unit tests for replay_pipeline.py's startup path.

- The CLI live-race guard (Day 43 Part 3.2): `python -m
  backend.scripts.replay_pipeline` must refuse to run while a real live race
  is being ingested, since a replay and a live ingestor write the same Redis
  timing/position keys.
- FastF1 cache setup: _load_fastf1_session must create the cache directory
  (FastF1's enable_cache won't), or a fresh container crashes on start.
"""

import argparse
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import fastf1
import pytest
import redis

from backend.scripts import replay_pipeline
from backend.services.live_race_detection import LiveRaceStatus


@pytest.fixture(autouse=True)
def _stub_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real Redis connection — the guard only needs a client to close()."""
    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: MagicMock())


@pytest.mark.unit
def test_guard_exits_when_live_race_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        replay_pipeline,
        "detect_live_race_sync",
        lambda _client: LiveRaceStatus(True, "live timing feed active for 2026 round 10"),
    )
    with pytest.raises(SystemExit) as exc_info:
        replay_pipeline._guard_against_live_race()
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_guard_passes_when_no_live_race(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        replay_pipeline, "detect_live_race_sync", lambda _client: LiveRaceStatus(False, None)
    )
    replay_pipeline._guard_against_live_race()  # must not raise


@pytest.mark.unit
def test_main_aborts_before_replay_when_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        replay_pipeline,
        "_parse_args",
        lambda: argparse.Namespace(
            session_id=uuid.uuid4(),
            rate=5,
            no_alert_worker=True,
            limit=None,
            start_lap=None,
            end_lap=None,
        ),
    )
    monkeypatch.setattr(
        replay_pipeline,
        "detect_live_race_sync",
        lambda _client: LiveRaceStatus(True, "live timing feed active for 2026 round 5"),
    )
    replay_mock = MagicMock()
    monkeypatch.setattr(replay_pipeline, "replay", replay_mock)

    with pytest.raises(SystemExit):
        replay_pipeline.main()

    replay_mock.assert_not_called()


@pytest.mark.unit
def test_load_fastf1_session_creates_missing_cache_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache_dir = tmp_path / "fastf1_cache"  # deliberately does not exist yet
    monkeypatch.setattr(
        replay_pipeline, "get_ml_settings", lambda: MagicMock(fastf1_cache_dir=str(cache_dir))
    )
    monkeypatch.setattr(fastf1.Cache, "enable_cache", lambda _dir: None)
    fake_session = MagicMock()
    monkeypatch.setattr(fastf1, "get_session", lambda *_a, **_k: fake_session)

    result = replay_pipeline._load_fastf1_session(2026, 10, "R")

    assert cache_dir.is_dir()
    fake_session.load.assert_called_once()
    assert result is fake_session
