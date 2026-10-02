"""Unit tests for scripts/_replay_common.py: the live-race guard, the SIGTERM
handler and the replay Redis keys shared by replay_pipeline and replay_playback."""

import signal
from unittest.mock import MagicMock

import pytest
import redis

from backend.scripts import _replay_common
from backend.services.live_race_detection import LiveRaceStatus


@pytest.fixture(autouse=True)
def _stub_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real Redis connection — the guard only needs a client to close()."""
    monkeypatch.setattr(redis.Redis, "from_url", lambda *args, **kwargs: MagicMock())


@pytest.mark.unit
def test_guard_exits_when_live_race_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _replay_common,
        "detect_live_race_sync",
        lambda _client: LiveRaceStatus(True, "live timing feed active for 2026 round 10"),
    )
    with pytest.raises(SystemExit) as exc_info:
        _replay_common.guard_against_live_race()
    assert exc_info.value.code == 1


@pytest.mark.unit
def test_guard_passes_when_no_live_race(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _replay_common, "detect_live_race_sync", lambda _client: LiveRaceStatus(False, None)
    )
    _replay_common.guard_against_live_race()  # must not raise


@pytest.mark.unit
def test_sigterm_handler_raises_keyboard_interrupt() -> None:
    with pytest.raises(KeyboardInterrupt):
        _replay_common.reraise_sigterm_as_interrupt(signal.SIGTERM, None)


@pytest.mark.unit
def test_keys_match_what_the_live_ingestor_and_the_api_read() -> None:
    assert _replay_common.gaps_key(2026, 9) == "f1:2026:9:gaps"
    assert _replay_common.position_key(2026, 9, "44") == "f1:2026:9:car:44:position"
    assert _replay_common.car_number_key(2026, 9, "d1") == "f1:2026:9:driver:d1:car_number"
