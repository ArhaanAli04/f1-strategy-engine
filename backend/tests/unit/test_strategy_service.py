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
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis as fakeredis_lib
import numpy as np
import pytest

from backend.core.exceptions import NotFoundError
from backend.schemas.strategy_schema import PitWindowResponse
from backend.services import cache_service, strategy_service
from backend.services.ml.tire_deg_model import FEATURE_COLUMNS, _build_pipeline

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


def _current_state_side_effects(
    lap: SimpleNamespace, total_laps: int, circuit_id: uuid.UUID
) -> list[MagicMock]:
    """The 3 db.execute() calls _current_state makes, in order: lap, total_laps, circuit."""
    return [_lap_result(lap), _scalar_result(total_laps), _scalar_result(circuit_id)]


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
    rng = np.random.default_rng(seed)
    n = 100
    tyre_age_idx = FEATURE_COLUMNS.index("tyre_age_laps")
    features = rng.random((n, len(FEATURE_COLUMNS)))
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
        _scalar_result(circuit_id),
    ]

    # Constant high pit probability — crosses ALERT_THRESHOLD on the very first
    # offset, giving a deterministic predicted_pit_lap for every driver.
    pit_model = MagicMock()
    pit_model.predict_proba.return_value = np.array([[0.2, 0.8]])
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
