"""Unit tests for services/demo_service.py."""

import json
import os
import signal
import sys
import uuid

import fakeredis as fakeredis_lib
import pytest

from backend.core.exceptions import ConflictError, NotFoundError, ValidationError
from backend.services import demo_service

_BRITISH_GP = uuid.UUID("7da820bf-5e8c-49bb-b19f-cdd88325af87")
_STATE_KEY = "f1:demo:replay:state"
# What ingest_live_session._publish_live_gaps writes — the only gaps payload
# detect_live_race treats as a live race.
_LIVE_GAPS_PAYLOAD = json.dumps({"session_id": "s", "gaps": [], "source": "live"})


class _FakeProc:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid


@pytest.fixture
def mock_launch(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, int, int]]:
    """Replace the real subprocess launch with a recorder returning a fake PID.

    Also stubs _process_is_alive to True so get_replay_status treats the fake
    PID as a running replay (the real check would find no such process on the
    CI host and self-heal the state away).
    """
    calls: list[tuple[uuid.UUID, int, int]] = []

    def _fake(session_id: uuid.UUID, start_lap: int, end_lap: int) -> _FakeProc:
        calls.append((session_id, start_lap, end_lap))
        return _FakeProc()

    monkeypatch.setattr(demo_service, "_launch_replay_subprocess", _fake)
    monkeypatch.setattr(demo_service, "_process_is_alive", lambda _pid: True)
    return calls


@pytest.mark.unit
def test_list_curated_sessions_returns_the_three() -> None:
    result = demo_service.list_curated_sessions()
    assert len(result.sessions) == 3
    assert _BRITISH_GP in {s.session_id for s in result.sessions}


@pytest.mark.unit
async def test_availability_true_when_no_live_race(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    result = await demo_service.get_replay_availability(fakeredis)
    assert result.available is True
    assert result.reason is None


@pytest.mark.unit
async def test_availability_false_when_live_race(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    await fakeredis.setex("f1:2026:10:gaps", 30, _LIVE_GAPS_PAYLOAD)
    result = await demo_service.get_replay_availability(fakeredis)
    assert result.available is False
    assert result.reason is not None


@pytest.mark.unit
async def test_status_not_running_without_state_key(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    result = await demo_service.get_replay_status(fakeredis)
    assert result.running is False


@pytest.mark.unit
async def test_start_rejects_unknown_session(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    mock_launch: list[tuple[uuid.UUID, int, int]],
) -> None:
    with pytest.raises(ValidationError):
        await demo_service.start_replay(fakeredis, uuid.uuid4())
    assert mock_launch == []


@pytest.mark.unit
async def test_start_rejects_when_live_race(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    mock_launch: list[tuple[uuid.UUID, int, int]],
) -> None:
    await fakeredis.setex("f1:2026:10:gaps", 30, _LIVE_GAPS_PAYLOAD)
    with pytest.raises(ConflictError):
        await demo_service.start_replay(fakeredis, _BRITISH_GP)
    assert mock_launch == []
    assert await fakeredis.get(_STATE_KEY) is None


@pytest.mark.unit
async def test_start_launches_and_records_state(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    mock_launch: list[tuple[uuid.UUID, int, int]],
) -> None:
    resp = await demo_service.start_replay(fakeredis, _BRITISH_GP)

    assert resp.session_id == _BRITISH_GP
    assert (resp.start_lap, resp.end_lap) == (43, 52)
    assert mock_launch == [(_BRITISH_GP, 43, 52)]

    raw = await fakeredis.get(_STATE_KEY)
    assert raw is not None
    state = json.loads(raw)
    assert state["pid"] == 4242
    assert state["race_name"] == resp.race_name
    assert 0 < await fakeredis.ttl(_STATE_KEY) <= 7200

    status = await demo_service.get_replay_status(fakeredis)
    assert status.running is True
    assert status.replay_id == resp.replay_id
    assert status.session_id == _BRITISH_GP


@pytest.mark.unit
async def test_start_conflicts_when_already_running(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    mock_launch: list[tuple[uuid.UUID, int, int]],
) -> None:
    await demo_service.start_replay(fakeredis, _BRITISH_GP)
    with pytest.raises(ConflictError):
        await demo_service.start_replay(fakeredis, _BRITISH_GP)
    assert len(mock_launch) == 1


@pytest.mark.unit
async def test_start_clears_claim_if_launch_fails(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(session_id: uuid.UUID, start_lap: int, end_lap: int) -> _FakeProc:
        raise OSError("fork failed")

    monkeypatch.setattr(demo_service, "_launch_replay_subprocess", _boom)
    with pytest.raises(OSError, match="fork failed"):
        await demo_service.start_replay(fakeredis, _BRITISH_GP)
    assert await fakeredis.get(_STATE_KEY) is None


@pytest.mark.unit
async def test_stop_raises_when_nothing_running(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    with pytest.raises(NotFoundError):
        await demo_service.stop_replay(fakeredis)


@pytest.mark.unit
async def test_stop_signals_pid_and_clears_state(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    mock_launch: list[tuple[uuid.UUID, int, int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    await demo_service.start_replay(fakeredis, _BRITISH_GP)
    resp = await demo_service.stop_replay(fakeredis)

    assert resp.stopped is True
    assert resp.session_id == _BRITISH_GP
    assert killed == [(4242, signal.SIGTERM)]
    assert await fakeredis.get(_STATE_KEY) is None


@pytest.mark.unit
async def test_stop_tolerates_already_dead_pid(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    mock_launch: list[tuple[uuid.UUID, int, int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _gone(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", _gone)

    await demo_service.start_replay(fakeredis, _BRITISH_GP)
    resp = await demo_service.stop_replay(fakeredis)

    assert resp.stopped is True
    assert await fakeredis.get(_STATE_KEY) is None


@pytest.mark.unit
async def test_status_self_heals_when_replay_pid_is_dead(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    mock_launch: list[tuple[uuid.UUID, int, int]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A replay that finished on its own never clears the state key. If its
    # PID is gone, get_replay_status must report not-running AND drop the key.
    await demo_service.start_replay(fakeredis, _BRITISH_GP)
    monkeypatch.setattr(demo_service, "_process_is_alive", lambda _pid: False)

    status = await demo_service.get_replay_status(fakeredis)

    assert status.running is False
    assert await fakeredis.get(_STATE_KEY) is None


@pytest.mark.unit
async def test_status_reports_running_while_replay_pid_is_alive(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    mock_launch: list[tuple[uuid.UUID, int, int]],
) -> None:
    await demo_service.start_replay(fakeredis, _BRITISH_GP)

    status = await demo_service.get_replay_status(fakeredis)

    assert status.running is True
    assert status.session_id == _BRITISH_GP
    assert await fakeredis.get(_STATE_KEY) is not None


@pytest.mark.unit
def test_process_is_alive_true_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert demo_service._process_is_alive(999999) is True


@pytest.mark.unit
def test_process_is_alive_false_when_child_is_reaped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "waitpid", lambda pid, _flags: (pid, 0))
    assert demo_service._process_is_alive(4242) is False


@pytest.mark.unit
def test_process_is_alive_false_when_pid_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    def _no_child(_pid: int, _flags: int) -> tuple[int, int]:
        raise ChildProcessError

    def _gone(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "waitpid", _no_child)
    monkeypatch.setattr(os, "kill", _gone)
    assert demo_service._process_is_alive(4242) is False


@pytest.mark.unit
def test_process_is_alive_false_when_zombie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    def _no_child(_pid: int, _flags: int) -> tuple[int, int]:
        raise ChildProcessError

    monkeypatch.setattr(os, "waitpid", _no_child)
    monkeypatch.setattr(os, "kill", lambda _pid, _sig: None)
    monkeypatch.setattr(demo_service, "_proc_is_zombie", lambda _pid: True)
    assert demo_service._process_is_alive(4242) is False


@pytest.mark.unit
def test_process_is_alive_true_when_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(os, "waitpid", lambda _pid, _flags: (0, 0))
    monkeypatch.setattr(os, "kill", lambda _pid, _sig: None)
    monkeypatch.setattr(demo_service, "_proc_is_zombie", lambda _pid: False)
    assert demo_service._process_is_alive(4242) is True
