"""Unit tests for services/strategy_service.py.

_load_models() is always monkeypatched to a synthetic model registry — real
.pkl files are never downloaded from S3 (see module docstring's note on why
services/ml pipelines are duplicated rather than imported from prediction_worker).
mock_db_session (AsyncMock spec'd to AsyncSession) stands in for the DB; the real
fakeredis fixture stands in for Redis so @cacheable's cache-aside logic runs for
real, not mocked. cache_service.cache_lock is stubbed out (see _stub_cache_lock
below) since fakeredis has no Lua/EVALSHA support, needed by redis-py's Lock to
release — the single-flight lock's real mechanics are covered by integration
tests against real Redis, not this tier.
"""

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis as fakeredis_lib
import joblib
import numpy as np
import pytest

from backend.core.exceptions import NotFoundError, ValidationError
from backend.schemas.strategy_schema import PitWindowResponse
from backend.services import cache_service, strategy_service
from backend.services.ml.tire_deg_model import (
    FEATURE_COLUMNS,
    CategoricalEncodingMaps,
    _build_pipeline,
)

SEASON = 2026
ROUND_NUMBER = 10


class _NoOpLock:
    async def acquire(self, *args: Any, **kwargs: Any) -> bool:
        return True

    async def release(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_cache_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_service, "cache_lock", lambda client, key: _NoOpLock())


def _fake_lap(lap_number: int, compound: str, tyre_age_laps: int, position: int) -> SimpleNamespace:
    return SimpleNamespace(
        lap_number=lap_number,
        compound=compound,
        tyre_age_laps=tyre_age_laps,
        position=position,
    )


def _lap_result(lap: SimpleNamespace) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = lap
    return result


def _scalar_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.scalar_one.return_value = value
    return result


def _one_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.one.return_value = value
    return result


def _current_state_side_effects(
    lap: SimpleNamespace,
    total_laps: int,
    circuit_id: uuid.UUID,
    circuit_name: str = "Test Circuit",
) -> list[MagicMock]:
    """The 3 db.execute() calls _current_state makes, in order: lap, total_laps, circuit."""
    return [
        _lap_result(lap),
        _scalar_result(total_laps),
        _one_result((circuit_id, circuit_name)),
    ]


def _fake_competitor_lap(
    driver_id: uuid.UUID, lap_number: int, compound: str, tyre_age_laps: int, position: int
) -> SimpleNamespace:
    return SimpleNamespace(
        driver_id=driver_id,
        lap_number=lap_number,
        compound=compound,
        tyre_age_laps=tyre_age_laps,
        position=position,
    )


def _scalars_all_result(items: list[Any]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _fit_slope_pipeline(slope: float, seed: int) -> Any:
    """A synthetic tire_deg pipeline where predicted delta grows ~linearly with tyre_age_laps."""
    return _fit_slope_pipeline_with_n_features(len(FEATURE_COLUMNS), slope, seed)


def _fit_slope_pipeline_with_n_features(n_features: int, slope: float, seed: int) -> Any:
    """Same as _fit_slope_pipeline but with a caller-chosen feature count — used to
    simulate a schema-drifted model (see tire_deg_model.pipeline_feature_count).
    """
    rng = np.random.default_rng(seed)
    n = 100
    tyre_age_idx = min(FEATURE_COLUMNS.index("tyre_age_laps"), n_features - 1)
    features = rng.random((n, n_features))
    features[:, tyre_age_idx] = rng.uniform(0, 40, n)
    target = slope * features[:, tyre_age_idx] + rng.normal(0, 0.05, n)
    pipeline = _build_pipeline()
    pipeline.fit(features, target)
    return pipeline


@pytest.mark.unit
async def test_optimal_pit_window_returns_sorted_by_time(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=10, compound="MEDIUM", tyre_age_laps=10, position=3)
    mock_db_session.execute.side_effect = _current_state_side_effects(
        lap, total_laps=50, circuit_id=circuit_id
    )

    pipeline = _fit_slope_pipeline(slope=0.2, seed=1)
    monkeypatch.setattr(
        strategy_service,
        "_load_models",
        lambda: {
            "tire_deg_soft.pkl": pipeline,
            "tire_deg_medium.pkl": pipeline,
            "tire_deg_hard.pkl": pipeline,
        },
    )

    candidates = await strategy_service.get_optimal_pit_window(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )

    assert len(candidates) >= 2
    assert (
        candidates[0]["projected_total_delta_seconds"]
        < candidates[1]["projected_total_delta_seconds"]
    )


@pytest.mark.unit
async def test_undercut_returns_positive_when_gap_favourable(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    target_driver_id = uuid.uuid4()
    circuit_id = uuid.uuid4()

    now_lap = _fake_lap(lap_number=20, compound="MEDIUM", tyre_age_laps=1, position=2)
    next_lap = _fake_lap(lap_number=20, compound="MEDIUM", tyre_age_laps=30, position=1)
    mock_db_session.execute.side_effect = [
        *_current_state_side_effects(now_lap, total_laps=50, circuit_id=circuit_id),
        *_current_state_side_effects(next_lap, total_laps=50, circuit_id=circuit_id),
        _scalar_result(1800.0),  # now driver's cumulative race time
        _scalar_result(1800.0),  # target driver's cumulative race time (deficit == 0)
    ]

    # Steep slope: target's extra lap at tyre_age=30 costs far more than the now
    # driver's fresh laps, so pitting now should clearly gain track position.
    pipeline = _fit_slope_pipeline(slope=0.5, seed=2)
    monkeypatch.setattr(strategy_service, "_load_models", lambda: {"tire_deg_medium.pkl": pipeline})

    result = await strategy_service.get_undercut_score(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id, target_driver_id
    )

    assert result["probability_pit_now_gains_position"] > 0.5
    assert result["projected_gap_seconds"] > 0
    assert result["recommended_action"] == "PIT NOW"


@pytest.mark.unit
async def test_cache_is_checked_before_compute(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    cached_candidates = [
        {"pit_lap": 15, "window_start": 11, "window_end": 25, "projected_total_delta_seconds": 12.5}
    ]
    key = strategy_service._key_pit_window(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )
    await cache_service.cache_set(fakeredis, key, cached_candidates, ttl=30)

    load_models_mock = MagicMock(side_effect=AssertionError("must not compute on a cache hit"))
    monkeypatch.setattr(strategy_service, "_load_models", load_models_mock)

    result = await strategy_service.get_optimal_pit_window(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )

    assert result == cached_candidates
    load_models_mock.assert_not_called()
    mock_db_session.execute.assert_not_called()


@pytest.mark.unit
async def test_cache_miss_triggers_computation_and_writes_cache(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=10, compound="MEDIUM", tyre_age_laps=10, position=3)
    mock_db_session.execute.side_effect = _current_state_side_effects(
        lap, total_laps=50, circuit_id=circuit_id
    )

    pipeline = _fit_slope_pipeline(slope=0.2, seed=3)
    monkeypatch.setattr(
        strategy_service,
        "_load_models",
        lambda: {
            "tire_deg_soft.pkl": pipeline,
            "tire_deg_medium.pkl": pipeline,
            "tire_deg_hard.pkl": pipeline,
        },
    )

    key = strategy_service._key_pit_window(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )
    assert await fakeredis.get(key) is None

    result = await strategy_service.get_optimal_pit_window(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )

    assert len(result) > 0
    assert await fakeredis.get(key) is not None


@pytest.mark.unit
async def test_get_competitor_predicted_strategy_returns_prediction_per_driver(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    driver_a = uuid.uuid4()
    driver_b = uuid.uuid4()
    laps = [
        _fake_competitor_lap(
            driver_a, lap_number=20, compound="MEDIUM", tyre_age_laps=15, position=1
        ),
        _fake_competitor_lap(
            driver_b, lap_number=18, compound="MEDIUM", tyre_age_laps=10, position=2
        ),
    ]
    mock_db_session.execute.side_effect = [
        _scalars_all_result(laps),
        _one_result((circuit_id, "Test Circuit")),
    ]

    # Constant high pit probability — crosses ALERT_THRESHOLD on the very first
    # offset, giving a deterministic predicted_pit_lap for every driver.
    pit_model = MagicMock()
    pit_model.predict_proba.side_effect = lambda features: np.tile([0.2, 0.8], (len(features), 1))
    tire_pipeline = _fit_slope_pipeline(slope=0.2, seed=9)
    monkeypatch.setattr(
        strategy_service,
        "_load_models",
        lambda: {"pit_predictor.pkl": pit_model, "tire_deg_medium.pkl": tire_pipeline},
    )

    results = await strategy_service.get_competitor_predicted_strategy(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id
    )

    assert {r["driver_id"] for r in results} == {str(driver_a), str(driver_b)}
    for entry in results:
        assert entry["pit_probability"] == pytest.approx(0.8)
        assert entry["predicted_pit_lap"] > 0


@pytest.mark.unit
async def test_get_pit_window_with_explanation_attaches_shap_to_top_candidate_only(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=10, compound="MEDIUM", tyre_age_laps=10, position=3)
    # get_optimal_pit_window's own _current_state call, then this function's own
    # second _current_state call — 3 db.execute() calls each, in order.
    mock_db_session.execute.side_effect = [
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
    ]

    pipeline = _fit_slope_pipeline(slope=0.2, seed=10)
    monkeypatch.setattr(
        strategy_service,
        "_load_models",
        lambda: {
            "tire_deg_soft.pkl": pipeline,
            "tire_deg_medium.pkl": pipeline,
            "tire_deg_hard.pkl": pipeline,
        },
    )

    responses = await strategy_service.get_pit_window_with_explanation(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )

    assert len(responses) >= 1
    assert responses[0].shap_explanation is not None
    assert len(responses[0].shap_explanation) > 0
    if len(responses) > 1:
        assert responses[1].shap_explanation is None


@pytest.mark.unit
async def test_resolve_season_round_returns_season_and_round(mock_db_session: AsyncMock) -> None:
    row_result = MagicMock()
    row_result.one_or_none.return_value = (2026, 12)
    mock_db_session.execute.return_value = row_result

    season, round_number = await strategy_service.resolve_season_round(
        mock_db_session, uuid.uuid4()
    )

    assert (season, round_number) == (2026, 12)


@pytest.mark.unit
async def test_resolve_season_round_raises_not_found_when_no_session(
    mock_db_session: AsyncMock,
) -> None:
    row_result = MagicMock()
    row_result.one_or_none.return_value = None
    mock_db_session.execute.return_value = row_result

    with pytest.raises(NotFoundError):
        await strategy_service.resolve_season_round(mock_db_session, uuid.uuid4())


# --- validate_current_lap ---
# See docs/simulator-issues-wet-model-and-position-context.md's Checkpoint-6
# follow-up finding: a current_lap of 68 was silently accepted for a session
# whose real race was 44 laps. mock_db_session.execute.side_effect below
# always supplies exactly 2 results in order — session-existence check, then
# the MAX(lap_number) query — matching validate_current_lap's own query order.


def _current_lap_check_side_effects(
    session_exists: bool, max_ingested_lap: int | None
) -> list[MagicMock]:
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = uuid.uuid4() if session_exists else None
    max_lap_result = MagicMock()
    max_lap_result.scalar_one_or_none.return_value = max_ingested_lap
    return [session_result, max_lap_result]


@pytest.mark.unit
async def test_validate_current_lap_raises_not_found_for_unknown_session(
    mock_db_session: AsyncMock,
) -> None:
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = None
    mock_db_session.execute.return_value = session_result

    with pytest.raises(NotFoundError):
        await strategy_service.validate_current_lap(mock_db_session, uuid.uuid4(), current_lap=1)


@pytest.mark.unit
async def test_validate_current_lap_allows_pre_race_what_if_with_no_lap_data(
    mock_db_session: AsyncMock,
) -> None:
    """No lap_data at all for a real session — current_lap=1 (the earliest
    Field(ge=1) even allows) must be accepted, matching
    test_simulate_returns_task_id's existing zero-lap-data scenario.
    """
    mock_db_session.execute.side_effect = _current_lap_check_side_effects(
        session_exists=True, max_ingested_lap=None
    )

    await strategy_service.validate_current_lap(mock_db_session, uuid.uuid4(), current_lap=1)


@pytest.mark.unit
async def test_validate_current_lap_rejects_current_lap_beyond_no_lap_data_ceiling(
    mock_db_session: AsyncMock,
) -> None:
    mock_db_session.execute.side_effect = _current_lap_check_side_effects(
        session_exists=True, max_ingested_lap=None
    )

    with pytest.raises(ValidationError):
        await strategy_service.validate_current_lap(mock_db_session, uuid.uuid4(), current_lap=2)


@pytest.mark.unit
async def test_validate_current_lap_allows_one_past_real_progress(
    mock_db_session: AsyncMock,
) -> None:
    """Belgian GP-shaped scenario: 44 real laps ingested, current_lap=45 (one
    past — "currently completing the next lap") must be accepted.
    """
    mock_db_session.execute.side_effect = _current_lap_check_side_effects(
        session_exists=True, max_ingested_lap=44
    )

    await strategy_service.validate_current_lap(mock_db_session, uuid.uuid4(), current_lap=45)


@pytest.mark.unit
async def test_validate_current_lap_rejects_current_lap_beyond_real_progress(
    mock_db_session: AsyncMock,
) -> None:
    """The exact bug this fix closes: current_lap=68 for a 44-lap race."""
    mock_db_session.execute.side_effect = _current_lap_check_side_effects(
        session_exists=True, max_ingested_lap=44
    )

    with pytest.raises(ValidationError):
        await strategy_service.validate_current_lap(mock_db_session, uuid.uuid4(), current_lap=68)


@pytest.mark.unit
async def test_session_wrappers_resolve_season_round_then_delegate(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    target_driver_id = uuid.uuid4()

    async def _fake_resolve(db: Any, sid: uuid.UUID) -> tuple[int, int]:
        assert sid == session_id
        return SEASON, ROUND_NUMBER

    monkeypatch.setattr(strategy_service, "resolve_season_round", _fake_resolve)

    sentinel_pit_window = [
        PitWindowResponse(
            pit_lap=20, window_start=11, window_end=25, projected_total_delta_seconds=5.0
        )
    ]
    pit_window_mock = AsyncMock(return_value=sentinel_pit_window)
    monkeypatch.setattr(strategy_service, "get_pit_window_with_explanation", pit_window_mock)
    pit_window_result = await strategy_service.get_pit_window_for_session(
        fakeredis, mock_db_session, session_id, driver_id
    )
    pit_window_mock.assert_awaited_once_with(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )
    assert pit_window_result == sentinel_pit_window

    undercut_mock = AsyncMock(
        return_value={
            "target_driver_id": str(target_driver_id),
            "recommended_action": "PIT NOW",
            "probability_pit_now_gains_position": 0.9,
            "projected_gap_seconds": 1.2,
            "n_laps_projected": 5,
        }
    )
    monkeypatch.setattr(strategy_service, "get_undercut_score", undercut_mock)
    undercut_response = await strategy_service.get_undercut_for_session(
        fakeredis, mock_db_session, session_id, driver_id, target_driver_id
    )
    undercut_mock.assert_awaited_once_with(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id, target_driver_id
    )
    assert undercut_response.recommended_action == "PIT NOW"

    competitor_mock = AsyncMock(
        return_value=[
            {"driver_id": str(driver_id), "predicted_pit_lap": 20, "pit_probability": 0.7}
        ]
    )
    monkeypatch.setattr(strategy_service, "get_competitor_predicted_strategy", competitor_mock)
    overview = await strategy_service.get_strategy_overview_for_session(
        fakeredis, mock_db_session, session_id
    )
    competitor_mock.assert_awaited_once_with(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id
    )
    assert overview.session_id == session_id
    assert len(overview.drivers) == 1


@pytest.mark.unit
async def test_prediction_history_maps_optimal_pit_lap_and_orders_query(
    mock_db_session: AsyncMock,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    created_at = SimpleNamespace()  # placeholder, only identity matters below
    row = SimpleNamespace(
        lap_number=12,
        optimal_pit_lap=24,
        pit_probability=0.6,
        undercut_score=0.3,
        overcut_score=0.1,
        created_at=created_at,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [row]
    mock_db_session.execute.return_value = result

    history = await strategy_service.get_strategy_prediction_history(
        mock_db_session, session_id, driver_id
    )

    assert history == [
        {
            "lap_number": 12,
            "predicted_pit_lap": 24,  # renamed from the model's optimal_pit_lap
            "pit_probability": 0.6,
            "undercut_score": 0.3,
            "overcut_score": 0.1,
            "created_at": created_at,
        }
    ]
    query = mock_db_session.execute.call_args.args[0]
    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "lap_number" in compiled
    assert "NULLS LAST" in compiled.upper()


@pytest.mark.unit
async def test_prediction_history_empty_when_no_predictions(
    mock_db_session: AsyncMock,
) -> None:
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    mock_db_session.execute.return_value = result

    history = await strategy_service.get_strategy_prediction_history(
        mock_db_session, uuid.uuid4(), uuid.uuid4()
    )

    assert history == []


@pytest.mark.unit
async def test_strategy_prediction_history_for_session_shapes_response(
    mock_db_session: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    created_at = datetime.now(UTC)
    history_mock = AsyncMock(
        return_value=[
            {
                "lap_number": 5,
                "predicted_pit_lap": 22,
                "pit_probability": 0.4,
                "undercut_score": 0.2,
                "overcut_score": 0.1,
                "created_at": created_at,
            }
        ]
    )
    monkeypatch.setattr(strategy_service, "get_strategy_prediction_history", history_mock)

    response = await strategy_service.get_strategy_prediction_history_for_session(
        mock_db_session, session_id, driver_id
    )

    history_mock.assert_awaited_once_with(mock_db_session, session_id, driver_id)
    assert response.session_id == session_id
    assert response.driver_id == driver_id
    assert len(response.predictions) == 1
    assert response.predictions[0].lap_number == 5
    assert response.predictions[0].predicted_pit_lap == 22


def _one_or_none_result(value: Any) -> MagicMock:
    result = MagicMock()
    result.one_or_none.return_value = value
    return result


@pytest.mark.unit
async def test_get_last_ingested_session_shapes_the_newest_row(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id = uuid.uuid4()
    mock_db_session.execute.return_value = _one_or_none_result(
        (session_id, 2026, 12, "Dutch Grand Prix", "Circuit Zandvoort", date(2026, 8, 23))
    )

    response = await strategy_service.get_last_ingested_session(fakeredis, mock_db_session)

    assert response.session_id == session_id
    assert response.season == 2026
    assert response.round_number == 12
    assert response.event_name == "Dutch Grand Prix"
    assert response.circuit_name == "Circuit Zandvoort"
    assert response.race_date == date(2026, 8, 23)


@pytest.mark.unit
async def test_get_last_ingested_session_tolerates_null_event_name(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id = uuid.uuid4()
    mock_db_session.execute.return_value = _one_or_none_result(
        (session_id, 2021, 13, None, "Circuit Zandvoort", date(2021, 9, 5))
    )

    response = await strategy_service.get_last_ingested_session(fakeredis, mock_db_session)

    assert response.event_name is None
    assert response.circuit_name == "Circuit Zandvoort"


@pytest.mark.unit
async def test_get_last_ingested_session_raises_when_no_ingested_races(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    mock_db_session.execute.return_value = _one_or_none_result(None)

    with pytest.raises(NotFoundError):
        await strategy_service.get_last_ingested_session(fakeredis, mock_db_session)


@pytest.mark.unit
async def test_get_last_ingested_session_query_filters_completed_status(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """B1 mitigation (docs/simulator-issues-wet-model-and-position-context.md):
    a scheduled/in-progress session (e.g. a partial live-ingestion dry run like
    Dutch GP 2026 Round 12) must never be picked, even with the newest
    race_date and ingested lap_data — only Race.status == "completed" is
    eligible. Asserts the compiled SQL itself carries the filter, not just a
    mocked return value, since the mock would happily return the same row
    regardless of what query was actually built.
    """
    session_id = uuid.uuid4()
    captured_queries: list[Any] = []

    async def _execute_side_effect(query: Any, *args: Any, **kwargs: Any) -> MagicMock:
        captured_queries.append(query)
        return _one_or_none_result(
            (
                session_id,
                2026,
                10,
                "Belgian Grand Prix",
                "Circuit de Spa-Francorchamps",
                date(2026, 7, 26),
            )
        )

    mock_db_session.execute.side_effect = _execute_side_effect

    await strategy_service.get_last_ingested_session(fakeredis, mock_db_session)

    assert len(captured_queries) == 1
    compiled = str(captured_queries[0].compile(compile_kwargs={"literal_binds": True}))
    assert "status" in compiled
    assert "'completed'" in compiled


# --- _load_models: WET/INTER schema-mismatch alias (Checkpoint 3) ---
# See docs/simulator-issues-wet-model-and-position-context.md Part A. Unlike
# every other test in this file, this one exercises the REAL _load_models
# body (not a monkeypatched replacement) — it's the only test that needs to,
# since it's specifically testing what _load_models itself does with the
# freshly "downloaded" registry before handing it back.


@pytest.mark.unit
def test_load_models_aliases_schema_incompatible_wet_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_wet = _fit_slope_pipeline_with_n_features(n_features=8, slope=0.1, seed=200)
    inter = _fit_slope_pipeline_with_n_features(
        n_features=len(FEATURE_COLUMNS), slope=0.1, seed=201
    )
    other = _fit_slope_pipeline_with_n_features(
        n_features=len(FEATURE_COLUMNS), slope=0.1, seed=202
    )
    pipelines_by_filename = {"tire_deg_wet.pkl": stale_wet, "tire_deg_inter.pkl": inter}

    monkeypatch.setattr(strategy_service, "_download_from_s3", lambda filename: filename)
    # Patches the joblib module itself (not strategy_service.joblib) — both
    # reference the same module object, and reaching through another
    # module's imported attribute trips mypy --strict's --no-implicit-reexport.
    monkeypatch.setattr(joblib, "load", lambda path: pipelines_by_filename.get(path, other))
    monkeypatch.setattr(strategy_service, "_model_cache", {})
    # No sidecar for any filename here — this test is scoped to model aliasing
    # only; the encoding-maps side of the same aliasing call is covered by
    # test_load_models_aliases_encoding_maps_alongside_wet_pipeline below.
    monkeypatch.setattr(strategy_service, "_download_metrics_from_s3", lambda filename: None)
    monkeypatch.setattr(strategy_service, "_encoding_maps_cache", {})

    models = strategy_service._load_models()

    assert models["tire_deg_wet.pkl"] is inter
    assert models["tire_deg_inter.pkl"] is inter
    assert set(models) == set(strategy_service._MODEL_FILES)


@pytest.mark.unit
def test_load_models_populates_encoding_maps_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """_load_models() downloads each tire_deg model's own sidecar and parses its encoding maps."""
    pipeline = _fit_slope_pipeline(slope=0.1, seed=210)
    metrics_by_filename = {
        "tire_deg_soft.pkl": {
            "holdout_mae": 0.5,
            "driver_id_to_code": {"d1": 3},
            "circuit_name_to_code": {"Monza": 7},
        },
        "tire_deg_medium.pkl": None,  # legacy sidecar — predates this fix, no maps recorded
    }

    monkeypatch.setattr(strategy_service, "_download_from_s3", lambda filename: filename)
    monkeypatch.setattr(joblib, "load", lambda path: pipeline)
    monkeypatch.setattr(
        strategy_service,
        "_download_metrics_from_s3",
        lambda filename: metrics_by_filename.get(filename),
    )
    monkeypatch.setattr(strategy_service, "_model_cache", {})
    monkeypatch.setattr(strategy_service, "_encoding_maps_cache", {})

    strategy_service._load_models()
    maps = strategy_service._load_encoding_maps()

    assert maps["tire_deg_soft.pkl"] == CategoricalEncodingMaps(
        driver_id_to_code={"d1": 3}, circuit_name_to_code={"Monza": 7}
    )
    assert maps["tire_deg_medium.pkl"] is None
    assert "pit_predictor.pkl" not in maps  # only tire_deg_* filenames get an entry


@pytest.mark.unit
def test_load_models_aliases_encoding_maps_alongside_wet_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WET's model-schema alias also aliases its encoding-maps cache entry to INTER's real map.

    A stale WET sidecar's own (possibly present but schema-mismatched) map must not be
    used once its pipeline is aliased to INTER — using WET's own map would encode
    driver/circuit codes for a model that isn't actually running any more.
    """
    stale_wet = _fit_slope_pipeline_with_n_features(n_features=8, slope=0.1, seed=203)
    inter = _fit_slope_pipeline_with_n_features(
        n_features=len(FEATURE_COLUMNS), slope=0.1, seed=204
    )
    pipelines_by_filename = {"tire_deg_wet.pkl": stale_wet, "tire_deg_inter.pkl": inter}
    inter_maps = {
        "holdout_mae": 0.4,
        "driver_id_to_code": {"d1": 1},
        "circuit_name_to_code": {"Monza": 2},
    }
    metrics_by_filename = {
        "tire_deg_wet.pkl": {"holdout_mae": 5.0},  # stale sidecar, no maps of its own
        "tire_deg_inter.pkl": inter_maps,
    }

    monkeypatch.setattr(strategy_service, "_download_from_s3", lambda filename: filename)
    monkeypatch.setattr(joblib, "load", lambda path: pipelines_by_filename.get(path, inter))
    monkeypatch.setattr(
        strategy_service,
        "_download_metrics_from_s3",
        lambda filename: metrics_by_filename.get(filename),
    )
    monkeypatch.setattr(strategy_service, "_model_cache", {})
    monkeypatch.setattr(strategy_service, "_encoding_maps_cache", {})

    strategy_service._load_models()
    maps = strategy_service._load_encoding_maps()

    assert maps["tire_deg_wet.pkl"] == CategoricalEncodingMaps(
        driver_id_to_code={"d1": 1}, circuit_name_to_code={"Monza": 2}
    )
    assert maps["tire_deg_wet.pkl"] is maps["tire_deg_inter.pkl"]


@pytest.mark.unit
async def test_cumulative_race_time_prefers_session_elapsed_seconds(
    mock_db_session: AsyncMock,
) -> None:
    """A backfilled historical session's real session_elapsed_seconds must be
    returned directly — the SUM(lap_time_seconds) fallback query must never
    even run (asserted via call count, not just the returned value).
    """
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()

    latest_row_result = MagicMock()
    latest_row_result.scalar_one_or_none.return_value = 5231.627
    mock_db_session.execute.return_value = latest_row_result

    result = await strategy_service._cumulative_race_time(
        mock_db_session, session_id, driver_id, 52
    )

    assert result == pytest.approx(5231.627)
    mock_db_session.execute.assert_called_once()


@pytest.mark.unit
async def test_cumulative_race_time_falls_back_to_sum_when_session_elapsed_seconds_null(
    mock_db_session: AsyncMock,
) -> None:
    """A live-ingested session (session_elapsed_seconds never backfilled) or a
    driver with no laps yet through up_to_lap must fall back to the original
    SUM(lap_time_seconds) reconstruction, unchanged.
    """
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()

    latest_row_result = MagicMock()
    latest_row_result.scalar_one_or_none.return_value = None
    sum_result = MagicMock()
    sum_result.scalar_one.return_value = 4310.0
    mock_db_session.execute.side_effect = [latest_row_result, sum_result]

    result = await strategy_service._cumulative_race_time(
        mock_db_session, session_id, driver_id, 40
    )

    assert result == pytest.approx(4310.0)
    assert mock_db_session.execute.call_count == 2


@pytest.mark.unit
async def test_cumulative_race_time_defaults_to_zero_when_no_laps(
    mock_db_session: AsyncMock,
) -> None:
    """Neither source has a value at all (driver has no laps yet) — must
    default to 0.0, matching the pre-existing "no cumulative time yet" contract.
    """
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()

    latest_row_result = MagicMock()
    latest_row_result.scalar_one_or_none.return_value = None
    sum_result = MagicMock()
    sum_result.scalar_one.return_value = None
    mock_db_session.execute.side_effect = [latest_row_result, sum_result]

    result = await strategy_service._cumulative_race_time(mock_db_session, session_id, driver_id, 1)

    assert result == 0.0
