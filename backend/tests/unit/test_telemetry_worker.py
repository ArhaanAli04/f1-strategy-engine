"""Unit tests for workers/telemetry_worker.py.

Currently scoped to a single regression, in both of the module's two
persist functions: the dispose-on-exception bug (CLAUDE.md's Deferred
Wiring — same shape as, and originally flagged alongside,
prediction_worker._run_simulation's identical bug, fixed under item 1d).
_persist_tire_stint had the identical shape as _persist_lap, found while
fixing the latter and fixed alongside on request. No fixture in
conftest.py stands in for the `async with session_factory() as db:`
pattern itself (mock_db_session is a bare AsyncMock(spec=AsyncSession), not
something session_factory() would return as an async context manager) —
_FakeSession below is a minimal, purpose-built stand-in instead.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.schemas.telemetry_schema import LapDataCreate, TireStintCreate
from backend.workers import telemetry_worker


class _FakeSession:
    """A minimal async-context-manager stand-in for AsyncSession.

    execute() raises unconditionally — forcing an exception inside the
    `async with session_factory() as db:` block, the same technique that
    would have caught prediction_worker._run_simulation's dispose bug (item
    1d) directly, instead of it surfacing incidentally via a crashed test
    fixture teardown.
    """

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated DB failure")

    async def commit(self) -> None:
        pytest.fail("commit() must not be reached — execute() already raised")


def _sample_lap() -> LapDataCreate:
    return LapDataCreate(
        session_id=uuid.uuid4(),
        driver_id=uuid.uuid4(),
        lap_number=12,
        lap_time_seconds=91.2,
        compound="MEDIUM",
        tyre_age_laps=12,
    )


def _sample_stint() -> TireStintCreate:
    return TireStintCreate(
        session_id=uuid.uuid4(),
        driver_id=uuid.uuid4(),
        stint_number=1,
        compound="MEDIUM",
        start_lap=1,
    )


@pytest.mark.unit
async def test_persist_lap_disposes_engine_even_when_persist_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact bug: a pre-fix `await get_engine().dispose()` placed after
    (not wrapping) the `async with session_factory() as db:` block is
    silently skipped whenever that block raises. Forces exactly that and
    asserts dispose() still runs, and that the original exception still
    propagates (this is not exception-swallowing, just guaranteed cleanup).
    """
    monkeypatch.setattr(telemetry_worker, "_get_session_factory", lambda: _FakeSession)

    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()
    monkeypatch.setattr(telemetry_worker, "get_engine", lambda: mock_engine)

    publish_mock = MagicMock()
    monkeypatch.setattr(telemetry_worker, "_publish_lap_completed", publish_mock)

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        await telemetry_worker._persist_lap(_sample_lap())

    mock_engine.dispose.assert_awaited_once()
    # The pre-fix behavior this guards against: publish only ever ran on the
    # happy path, but a lap that failed to persist must never be announced
    # as completed either — unaffected by this fix, asserted here so a
    # future change can't silently start firing it on the failure path.
    publish_mock.assert_not_called()


@pytest.mark.unit
async def test_persist_lap_disposes_engine_and_publishes_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sibling happy-path case: dispose() still runs (same as before this
    fix) and _publish_lap_completed fires exactly once, confirming the fix
    didn't change success-path behavior.
    """

    class _SucceedingSession(_FakeSession):
        async def execute(self, *args: object, **kwargs: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(telemetry_worker, "_get_session_factory", lambda: _SucceedingSession)

    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()
    monkeypatch.setattr(telemetry_worker, "get_engine", lambda: mock_engine)

    publish_mock = MagicMock()
    monkeypatch.setattr(telemetry_worker, "_publish_lap_completed", publish_mock)

    lap = _sample_lap()
    await telemetry_worker._persist_lap(lap)

    mock_engine.dispose.assert_awaited_once()
    publish_mock.assert_called_once_with(lap)


@pytest.mark.unit
async def test_persist_tire_stint_disposes_engine_even_when_persist_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same regression as test_persist_lap_disposes_engine_even_when_persist_raises,
    for _persist_tire_stint — the identical bug shape, found while fixing the
    lap version, fixed alongside on request.
    """
    monkeypatch.setattr(telemetry_worker, "_get_session_factory", lambda: _FakeSession)

    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()
    monkeypatch.setattr(telemetry_worker, "get_engine", lambda: mock_engine)

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        await telemetry_worker._persist_tire_stint(_sample_stint())

    mock_engine.dispose.assert_awaited_once()


@pytest.mark.unit
async def test_persist_tire_stint_disposes_engine_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sibling happy-path case: dispose() still runs, confirming the fix
    didn't change success-path behavior.
    """

    class _SucceedingSession(_FakeSession):
        async def execute(self, *args: object, **kwargs: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    monkeypatch.setattr(telemetry_worker, "_get_session_factory", lambda: _SucceedingSession)

    mock_engine = MagicMock()
    mock_engine.dispose = AsyncMock()
    monkeypatch.setattr(telemetry_worker, "get_engine", lambda: mock_engine)

    await telemetry_worker._persist_tire_stint(_sample_stint())

    mock_engine.dispose.assert_awaited_once()
