"""Unit tests for workers/idle_shutdown.py and its wiring into celery_app.py."""

import signal
from unittest.mock import MagicMock

import pytest

from backend.workers import celery_app, idle_shutdown
from backend.workers.idle_shutdown import IdleWatchdog, start_idle_watchdog

LIMIT_SECONDS = 600.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _watchdog() -> tuple[IdleWatchdog, FakeClock, MagicMock]:
    clock, on_idle = FakeClock(), MagicMock()
    return IdleWatchdog(LIMIT_SECONDS, on_idle=on_idle, clock=clock), clock, on_idle


@pytest.mark.unit
def test_exits_after_the_idle_limit_with_no_task() -> None:
    watchdog, clock, on_idle = _watchdog()

    clock.now += LIMIT_SECONDS - 1
    assert watchdog.check() is False
    clock.now += 1
    assert watchdog.check() is True

    on_idle.assert_called_once()


@pytest.mark.unit
def test_never_exits_while_a_task_is_running() -> None:
    watchdog, clock, on_idle = _watchdog()

    watchdog.task_started()
    clock.now += LIMIT_SECONDS * 10  # a very long simulation

    assert watchdog.check() is False
    on_idle.assert_not_called()


@pytest.mark.unit
def test_the_idle_clock_restarts_when_a_task_finishes() -> None:
    watchdog, clock, on_idle = _watchdog()
    clock.now += LIMIT_SECONDS - 10
    watchdog.task_started()
    clock.now += 60
    watchdog.task_finished()

    clock.now += LIMIT_SECONDS - 1
    assert watchdog.check() is False
    clock.now += 1
    assert watchdog.check() is True
    on_idle.assert_called_once()


@pytest.mark.unit
def test_fires_only_once() -> None:
    watchdog, clock, on_idle = _watchdog()
    clock.now += LIMIT_SECONDS

    assert watchdog.check() is True
    clock.now += LIMIT_SECONDS
    assert watchdog.check() is False
    on_idle.assert_called_once()


@pytest.mark.unit
def test_an_unmatched_finish_does_not_go_below_zero_running_tasks() -> None:
    watchdog, clock, on_idle = _watchdog()
    watchdog.task_finished()  # e.g. a postrun with no prerun seen
    watchdog.task_started()

    clock.now += LIMIT_SECONDS
    assert watchdog.check() is False  # the started task is still running
    on_idle.assert_not_called()


@pytest.mark.unit
def test_run_returns_once_it_has_fired(monkeypatch: pytest.MonkeyPatch) -> None:
    watchdog, clock, on_idle = _watchdog()
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    monkeypatch.setattr("backend.workers.idle_shutdown.time.sleep", fake_sleep)

    watchdog.run(poll_seconds=100.0)

    assert len(sleeps) == 6  # 600 s limit reached on the 7th check
    on_idle.assert_called_once()


@pytest.mark.unit
def test_stop_this_worker_sends_itself_sigterm(monkeypatch: pytest.MonkeyPatch) -> None:
    kill = MagicMock()
    monkeypatch.setattr("backend.workers.idle_shutdown.os.kill", kill)
    monkeypatch.setattr("backend.workers.idle_shutdown.os.getpid", lambda: 4242)

    idle_shutdown.stop_this_worker()

    kill.assert_called_once_with(4242, signal.SIGTERM)


@pytest.mark.unit
@pytest.mark.parametrize("minutes", [0, -1])
def test_off_when_not_configured(monkeypatch: pytest.MonkeyPatch, minutes: int) -> None:
    thread = MagicMock()
    monkeypatch.setattr("backend.workers.idle_shutdown.threading.Thread", thread)

    assert start_idle_watchdog(minutes) is None
    thread.assert_not_called()


@pytest.mark.unit
def test_on_when_configured_starts_a_daemon_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    thread = MagicMock()
    monkeypatch.setattr("backend.workers.idle_shutdown.threading.Thread", thread)

    watchdog = start_idle_watchdog(10)

    assert watchdog is not None
    assert thread.call_args.kwargs["daemon"] is True
    assert thread.call_args.kwargs["target"] == watchdog.run
    thread.return_value.start.assert_called_once()


@pytest.mark.unit
def test_celery_task_signals_report_to_the_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    watchdog = MagicMock()
    monkeypatch.setattr(celery_app, "_idle_watchdog", watchdog)

    celery_app._on_task_prerun(task_id="t1")
    celery_app._on_task_postrun(task_id="t1", task=MagicMock(name="task"), state="SUCCESS")

    watchdog.task_started.assert_called_once()
    watchdog.task_finished.assert_called_once()


@pytest.mark.unit
def test_celery_task_signals_work_without_a_watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(celery_app, "_idle_watchdog", None)

    celery_app._on_task_prerun(task_id="t2")
    celery_app._on_task_postrun(task_id="t2", task=MagicMock(name="task"), state="SUCCESS")
