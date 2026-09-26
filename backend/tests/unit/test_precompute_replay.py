"""Unit tests for scripts/precompute_replay.py.

A small hand-built laps table stands in for a loaded FastF1 session: the
helpers only read its laps DataFrame, its drivers list and get_driver(). The
run's tests stub the prediction and alert code and use fakeredis, so nothing
touches a real database, Redis, S3 or FastF1.
"""

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis as fakeredis_lib
import pandas as pd
import pytest
import redis.asyncio as redis_asyncio

from backend.core.exceptions import NotFoundError
from backend.models.replay import (
    ReplayAlertEvent,
    ReplayCarNumber,
    ReplayGapSnapshot,
    ReplayLapTiming,
)
from backend.models.strategy import StrategyPrediction
from backend.scripts import precompute_replay
from backend.services import alert_service
from backend.services.alert_service import LapUndercutAlert
from backend.workers import prediction_worker

_SESSION_ID = uuid.uuid4()
LEC, HAM, VER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
_CODES = {"LEC": LEC, "HAM": HAM, "VER": VER}
_NUMBERS = {"16": "LEC", "44": "HAM", "1": "VER", "99": "XXX"}  # XXX: unknown to our DB


def _laps() -> pd.DataFrame:
    """Laps 1-3: LEC leads, HAM second; VER stops on lap 3 (no finish time).

    Also a lap for a driver our DB does not know (XXX), to be ignored.
    """
    rows = [
        # Driver, LapNumber, LapStartTime, Time, Position
        ("LEC", 1, 100.0, 190.0, 1),
        ("HAM", 1, 100.0, 191.5, 2),
        ("VER", 1, 100.0, 193.0, 3),
        ("LEC", 2, 190.0, 280.0, 1),
        ("HAM", 2, 191.5, 283.0, 2),
        ("VER", 2, 193.0, 286.0, 3),
        ("LEC", 3, 280.0, 370.0, 1),
        ("HAM", 3, 283.0, 374.0, 2),
        ("VER", 3, 286.0, None, None),
        ("XXX", 2, 195.0, 290.0, 4),
    ]
    return pd.DataFrame(
        {
            "Driver": [r[0] for r in rows],
            "LapNumber": [float(r[1]) for r in rows],
            "LapStartTime": pd.to_timedelta([r[2] for r in rows], unit="s"),
            "Time": pd.to_timedelta([r[3] for r in rows], unit="s"),
            "Position": [float(r[4]) if r[4] is not None else float("nan") for r in rows],
        }
    )


def _session() -> Any:
    def _get_driver(number: str) -> dict[str, str]:
        return {"Abbreviation": _NUMBERS[number]}

    return SimpleNamespace(laps=_laps(), drivers=list(_NUMBERS), get_driver=_get_driver)


@pytest.mark.unit
def test_extract_lap_timings_covers_the_window_in_session_seconds() -> None:
    timings = precompute_replay.extract_lap_timings(_laps(), _CODES, 2, 3)

    assert [(t.lap_number, t.driver_id) for t in timings] == [
        (2, HAM),
        (2, LEC),
        (2, VER),
        (3, HAM),
        (3, LEC),
        (3, VER),
    ]
    assert timings[1] == precompute_replay.LapTiming(LEC, 2, 190.0, 280.0)


@pytest.mark.unit
def test_extract_lap_timings_keeps_a_lap_with_no_finish_time_as_none() -> None:
    timings = precompute_replay.extract_lap_timings(_laps(), _CODES, 3, 3)

    stopped = next(t for t in timings if t.driver_id == VER)
    assert stopped.lap_start_seconds == 286.0
    assert stopped.lap_end_seconds is None


@pytest.mark.unit
def test_extract_lap_timings_ignores_drivers_not_in_the_db() -> None:
    timings = precompute_replay.extract_lap_timings(_laps(), _CODES, 1, 3)
    assert {t.driver_id for t in timings} == {LEC, HAM, VER}


@pytest.mark.unit
def test_build_gap_snapshots_starts_one_lap_before_the_window() -> None:
    snapshots = precompute_replay.build_gap_snapshots(_session(), _CODES, _SESSION_ID, 2, 3)

    assert sorted(snapshots) == [1, 2, 3]
    lap2 = snapshots[2]
    assert lap2["session_id"] == str(_SESSION_ID)
    assert lap2["source"] == "replay"
    assert [g["driver_id"] for g in lap2["gaps"]] == [str(LEC), str(HAM), str(VER)]
    assert [g["gap_to_ahead_seconds"] for g in lap2["gaps"]] == [0.0, 3.0, 3.0]


@pytest.mark.unit
def test_build_gap_snapshots_leaves_out_a_car_with_no_time_on_that_lap() -> None:
    snapshots = precompute_replay.build_gap_snapshots(_session(), _CODES, _SESSION_ID, 3, 3)

    assert [g["driver_id"] for g in snapshots[3]["gaps"]] == [str(LEC), str(HAM)]


@pytest.mark.unit
def test_build_gap_snapshots_does_not_go_below_lap_one() -> None:
    snapshots = precompute_replay.build_gap_snapshots(_session(), _CODES, _SESSION_ID, 1, 1)
    assert sorted(snapshots) == [1]


@pytest.mark.unit
def test_extract_car_numbers_maps_known_drivers_in_number_order() -> None:
    assert precompute_replay.extract_car_numbers(_session(), _CODES) == [
        precompute_replay.CarNumber(VER, "1"),
        precompute_replay.CarNumber(LEC, "16"),
        precompute_replay.CarNumber(HAM, "44"),
    ]


# --- The run ---------------------------------------------------------------


_TARGET = precompute_replay.CuratedTarget(
    session_id=_SESSION_ID,
    race_name="Test Grand Prix",
    season=2026,
    round_number=9,
    start_lap=2,
    end_lap=3,
)
_GAPS_KEY = "f1:2026:9:gaps"


def _db_lap(driver_id: uuid.UUID, lap_number: int) -> Any:
    return SimpleNamespace(
        session_id=_SESSION_ID,
        driver_id=driver_id,
        lap_number=lap_number,
        lap_time_seconds=90.0,
        compound="MEDIUM",
        tyre_age_laps=10,
        is_valid=True,
        sector1_seconds=30.0,
        sector2_seconds=30.0,
        sector3_seconds=30.0,
    )


@pytest.fixture
def window_laps(monkeypatch: pytest.MonkeyPatch) -> None:
    """lap_data in the window: LEC and HAM on lap 2, only LEC on lap 3."""

    async def _laps(db: Any, target: Any) -> dict[int, list[Any]]:
        return {2: [_db_lap(LEC, 2), _db_lap(HAM, 2)], 3: [_db_lap(LEC, 3)]}

    monkeypatch.setattr(precompute_replay, "_laps_in_window", _laps)


def _record_alert_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[int, dict[uuid.UUID, float]]]:
    calls: list[tuple[int, dict[uuid.UUID, float]]] = []

    async def _find(
        db: Any, session_id: uuid.UUID, lap_number: int, scores: dict[uuid.UUID, float]
    ) -> list[LapUndercutAlert]:
        calls.append((lap_number, dict(scores)))
        return []

    monkeypatch.setattr(alert_service, "find_undercut_threats_at_lap", _find)
    return calls


@pytest.mark.unit
@pytest.mark.usefixtures("window_laps")
async def test_compute_session_publishes_each_laps_gaps_before_its_predictions(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps_lap_seen: list[tuple[int, int]] = []

    async def _predict(db: Any, redis_client: Any, context: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(await redis_client.get(_GAPS_KEY))
        gaps_lap_seen.append((context["lap_number"], payload["gaps"][0]["lap_number"]))
        return {"undercut_score": 0.7 if context["driver_id"] == str(HAM) else 0.1}

    monkeypatch.setattr(prediction_worker, "compute_prediction", _predict)
    alert_calls = _record_alert_calls(monkeypatch)

    result = await precompute_replay.compute_session(
        AsyncMock(), fakeredis, _TARGET, _session(), _CODES
    )

    assert gaps_lap_seen == [(2, 2), (2, 2), (3, 3)]
    assert [(driver, lap) for driver, lap, _ in result.predictions] == [
        (LEC, 2),
        (HAM, 2),
        (LEC, 3),
    ]
    assert alert_calls == [(2, {LEC: 0.1, HAM: 0.7}), (3, {LEC: 0.1})]
    assert sorted(result.gap_snapshots) == [1, 2, 3]
    assert len(result.lap_timings) == 6  # LEC, HAM, VER on laps 2 and 3 (from FastF1)
    assert len(result.car_numbers) == 3
    assert result.failed_predictions == 0
    assert await fakeredis.get(_GAPS_KEY) is None  # cleaned up at the end


@pytest.mark.unit
@pytest.mark.usefixtures("window_laps")
async def test_compute_session_counts_a_known_prediction_failure_and_carries_on(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _predict(db: Any, redis_client: Any, context: dict[str, Any]) -> dict[str, Any]:
        if context["driver_id"] == str(HAM):
            raise NotFoundError("No lap data for driver in session")
        return {"undercut_score": 0.2}

    monkeypatch.setattr(prediction_worker, "compute_prediction", _predict)
    alert_calls = _record_alert_calls(monkeypatch)

    result = await precompute_replay.compute_session(
        AsyncMock(), fakeredis, _TARGET, _session(), _CODES
    )

    assert result.failed_predictions == 1
    assert [(driver, lap) for driver, lap, _ in result.predictions] == [(LEC, 2), (LEC, 3)]
    assert alert_calls[0] == (2, {LEC: 0.2})  # the failed driver has no score


@pytest.mark.unit
@pytest.mark.usefixtures("window_laps")
async def test_compute_session_aborts_on_an_unexpected_error(
    fakeredis: fakeredis_lib.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _predict(db: Any, redis_client: Any, context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("model file corrupt")

    monkeypatch.setattr(prediction_worker, "compute_prediction", _predict)
    _record_alert_calls(monkeypatch)

    with pytest.raises(RuntimeError, match="model file corrupt"):
        await precompute_replay.compute_session(AsyncMock(), fakeredis, _TARGET, _session(), _CODES)


def _computed() -> precompute_replay.SessionPrecompute:
    result = precompute_replay.SessionPrecompute(target=_TARGET)
    result.predictions = [
        (
            LEC,
            2,
            {
                "optimal_pit_lap": 20,
                "pit_probability": 0.4,
                "undercut_score": 0.1,
                "overcut_score": 0.2,
                "tire_life_remaining": 10.0,
                "confidence_score": 0.6,
                "model_version": "production",
            },
        )
    ]
    result.lap_timings = [precompute_replay.LapTiming(LEC, 2, 190.0, 280.0)]
    result.gap_snapshots = {2: {"session_id": str(_SESSION_ID), "gaps": [], "source": "replay"}}
    result.alerts = [
        LapUndercutAlert(2, "UNDERCUT_THREAT", HAM, LEC, "Undercut threat: HAM on LEC (70%)", 0.7)
    ]
    result.car_numbers = [precompute_replay.CarNumber(LEC, "16")]
    return result


@pytest.mark.unit
async def test_write_session_replaces_every_old_row_then_inserts_in_one_commit() -> None:
    db = MagicMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    await precompute_replay.write_session(db, _computed())

    deleted = [
        str(call.args[0].compile(compile_kwargs={"literal_binds": True})).split(" WHERE")[0]
        for call in db.execute.await_args_list
    ]
    assert deleted == [
        "DELETE FROM strategy_predictions",
        "DELETE FROM replay_lap_timings",
        "DELETE FROM replay_gap_snapshots",
        "DELETE FROM replay_alert_events",
        "DELETE FROM replay_car_numbers",
    ]
    (rows,) = db.add_all.call_args.args
    assert [type(row) for row in rows] == [
        StrategyPrediction,
        ReplayLapTiming,
        ReplayGapSnapshot,
        ReplayAlertEvent,
        ReplayCarNumber,
    ]
    assert all(row.session_id == _SESSION_ID for row in rows)
    assert rows[0].lap_number == 2
    assert rows[0].undercut_score == 0.1
    db.commit.assert_awaited_once()
    method_order = [name for name, *_ in db.method_calls]
    last_delete = max(i for i, name in enumerate(method_order) if name == "execute")
    assert method_order.index("add_all") > last_delete


def _db_returning_rows(rows: list[tuple[int, int, uuid.UUID]]) -> AsyncMock:
    db = AsyncMock()
    db.execute.return_value = MagicMock(all=MagicMock(return_value=rows))
    return db


@pytest.mark.unit
async def test_resolve_targets_finds_the_curated_races_in_this_database() -> None:
    british, belgian = uuid.uuid4(), uuid.uuid4()
    db = _db_returning_rows([(2026, 10, belgian), (2026, 9, british)])

    targets = await precompute_replay.resolve_targets(db, None)

    assert [(t.race_name, t.session_id, t.start_lap, t.end_lap) for t in targets] == [
        ("British Grand Prix 2026", british, 43, 52),
        ("Belgian Grand Prix 2026", belgian, 14, 23),
    ]


@pytest.mark.unit
async def test_resolve_targets_can_pick_one_session_and_rejects_others() -> None:
    british, belgian = uuid.uuid4(), uuid.uuid4()
    rows = [(2026, 9, british), (2026, 10, belgian)]

    (only,) = await precompute_replay.resolve_targets(_db_returning_rows(rows), belgian)
    assert only.session_id == belgian

    with pytest.raises(ValueError, match="not a curated demo race"):
        await precompute_replay.resolve_targets(_db_returning_rows(rows), uuid.uuid4())


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def rollback(self) -> None:
        return None


@pytest.fixture
def stubbed_run(
    monkeypatch: pytest.MonkeyPatch, fakeredis: fakeredis_lib.FakeAsyncRedis
) -> dict[str, AsyncMock]:
    """Everything run() touches outside this module, stubbed."""
    monkeypatch.setattr(redis_asyncio, "from_url", lambda *a, **k: fakeredis)
    engine = MagicMock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr(precompute_replay, "get_engine", lambda: engine)
    monkeypatch.setattr(precompute_replay, "async_sessionmaker", lambda *a, **k: _FakeSession)
    monkeypatch.setattr(precompute_replay, "_production_model_dates", dict)
    monkeypatch.setattr(precompute_replay, "resolve_targets", AsyncMock(return_value=[_TARGET]))
    monkeypatch.setattr(precompute_replay, "_driver_code_map", AsyncMock(return_value=_CODES))
    monkeypatch.setattr(precompute_replay, "_load_fastf1_session", lambda *a: _session())
    compute = AsyncMock(return_value=_computed())
    write = AsyncMock()
    monkeypatch.setattr(precompute_replay, "compute_session", compute)
    monkeypatch.setattr(precompute_replay, "write_session", write)
    return {"compute": compute, "write": write, "dispose": engine.dispose}


@pytest.mark.unit
async def test_run_dry_run_computes_but_writes_nothing(stubbed_run: dict[str, AsyncMock]) -> None:
    results = await precompute_replay.run(None, dry_run=True)

    assert len(results) == 1
    stubbed_run["compute"].assert_awaited_once()
    stubbed_run["write"].assert_not_awaited()
    stubbed_run["dispose"].assert_awaited_once()


@pytest.mark.unit
async def test_run_writes_each_computed_session(stubbed_run: dict[str, AsyncMock]) -> None:
    await precompute_replay.run(None, dry_run=False)

    stubbed_run["write"].assert_awaited_once()


@pytest.mark.unit
async def test_run_refuses_while_a_demo_replay_is_running(
    stubbed_run: dict[str, AsyncMock], fakeredis: fakeredis_lib.FakeAsyncRedis
) -> None:
    await fakeredis.set("f1:demo:replay:state", "{}")

    with pytest.raises(RuntimeError, match="Demo Replay is running"):
        await precompute_replay.run(None, dry_run=False)

    stubbed_run["compute"].assert_not_awaited()
    stubbed_run["dispose"].assert_awaited_once()
