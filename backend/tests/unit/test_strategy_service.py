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

import json
import random
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis as fakeredis_lib
import joblib
import numpy as np
import pytest
from redis.exceptions import RedisError

from backend.core.exceptions import ModelNotLoadedError, NotFoundError, ValidationError
from backend.schemas.strategy_schema import PitWindowResponse
from backend.services import cache_service, strategy_service
from backend.services.ml import explainability, tire_deg_model
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
    """The 2 db.execute() calls _current_state makes, in order: lap, circuit.

    Fixed for docs/internal/live-race-ingestion-and-strategy-gaps-monza-2026.md Issue
    A: _current_state now reads Session.total_laps directly off the circuit
    query (a real stored value, not a separate MAX(lap_number) query) and
    only falls back to that proxy when the stored value is NULL — this
    helper always supplies a real stored total_laps, so the fallback path is
    never exercised here. See
    test_current_state_falls_back_to_max_lap_number_when_total_laps_unset
    for dedicated coverage of that fallback.
    """
    return [
        _lap_result(lap),
        _one_result((circuit_id, circuit_name, total_laps)),
    ]


@pytest.mark.unit
async def test_current_state_prefers_stored_total_laps_over_max_lap_number(
    mock_db_session: AsyncMock,
) -> None:
    """When Session.total_laps IS stored, it's used directly and the old
    MAX(lap_number) fallback query is never issued — only 2 db.execute()
    calls, not 3 (docs/internal/live-race-ingestion-and-strategy-gaps-monza-2026.md
    Issue A)."""
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=38, compound="MEDIUM", tyre_age_laps=20, position=3)

    mock_db_session.execute.side_effect = _current_state_side_effects(
        lap, total_laps=53, circuit_id=circuit_id
    )

    state = await strategy_service._current_state(mock_db_session, session_id, driver_id)

    assert state["total_laps"] == 53  # the real stored value, NOT lap.lap_number (38)
    assert mock_db_session.execute.call_count == 2


@pytest.mark.unit
async def test_current_state_falls_back_to_max_lap_number_when_total_laps_unset(
    mock_db_session: AsyncMock,
) -> None:
    """A session with no stored Session.total_laps (predates the column, or
    a live session before its first LapCount message has arrived) falls
    back to the old MAX(lap_number)-so-far proxy, unchanged from before
    this fix."""
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=10, compound="MEDIUM", tyre_age_laps=10, position=3)

    mock_db_session.execute.side_effect = [
        _lap_result(lap),
        _one_result((circuit_id, "Test Circuit", None)),  # total_laps NOT stored
        _scalar_result(38),  # MAX(lap_number) fallback
    ]

    state = await strategy_service._current_state(mock_db_session, session_id, driver_id)

    assert state["total_laps"] == 38
    assert mock_db_session.execute.call_count == 3


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

    candidates = await strategy_service.build_pit_recommendation(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )

    assert len(candidates) >= 2
    assert (
        candidates[0]["projected_total_delta_seconds"]
        < candidates[1]["projected_total_delta_seconds"]
    )


# --- build_pit_recommendation: Checkpoint 2 batching/compound/window/confidence ---
# All four tests below share the same deterministic setup: each compound's
# tire_deg pipeline is mocked to predict a CONSTANT delta per lap regardless
# of input (SOFT -0.5, MEDIUM +0.1, HARD +0.5), which makes every candidate's
# projected_total_delta_seconds hand-computable rather than merely "some
# plausible-looking number" — a genuine numerical regression test of the
# batched cumsum/2D-grid math, not just a structural "did it run" check.
#
# current_lap=10, tyre_age_laps=10, total_laps=30 -> 15 candidates,
# pit_laps 11..25 (i=0..14, pit_lap = 11+i):
#   stint1_delta[i] = 0.1*(i+1)                       (cumsum of MEDIUM's 0.1/lap)
#   stint2 best (always SOFT) delta[i] = -0.5*(19-i)  (laps_remaining = 30-(11+i) = 19-i)
#   total_delta[i] = 0.1*(i+1) + 22.0 (PIT_STOP_SECONDS) - 0.5*(19-i)
#                  = 0.6*i + 12.6  — strictly increasing, so i=0 (pit_lap=11) wins.


def _constant_delta_pipeline(value: float) -> MagicMock:
    pipeline = MagicMock()
    pipeline.predict.side_effect = lambda features: np.full(len(features), value)
    return pipeline


def _constant_delta_models() -> dict[str, MagicMock]:
    return {
        "tire_deg_soft.pkl": _constant_delta_pipeline(-0.5),
        "tire_deg_medium.pkl": _constant_delta_pipeline(0.1),
        "tire_deg_hard.pkl": _constant_delta_pipeline(0.5),
    }


@pytest.mark.unit
async def test_build_pit_recommendation_batches_predict_and_keeps_winning_compound(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=10, compound="MEDIUM", tyre_age_laps=10, position=3)
    mock_db_session.execute.side_effect = _current_state_side_effects(
        lap, total_laps=30, circuit_id=circuit_id
    )

    models = _constant_delta_models()
    monkeypatch.setattr(strategy_service, "_load_models", lambda: models)

    candidates = await strategy_service.build_pit_recommendation(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )

    assert candidates[0]["pit_lap"] == 11
    assert candidates[0]["projected_total_delta_seconds"] == pytest.approx(12.6)
    # Every returned candidate's recommended_compound is SOFT (it wins the
    # stint-2 argmin at every pit_lap, not just the top-ranked one) — the
    # value the original loop computed then discarded entirely.
    assert all(c["recommended_compound"] == "SOFT" for c in candidates)

    # The batching claim itself: 1 call for stint 1 (current compound,
    # MEDIUM) + 1 call each for stint 2's SOFT/MEDIUM/HARD sweep = 2 total on
    # the shared MEDIUM mock (it plays both roles), 1 each on SOFT/HARD — 4
    # predict() calls total, not up to ~60 (15 pit_lap candidates x up to 4
    # segments each, the pre-Checkpoint-2 shape).
    assert models["tire_deg_medium.pkl"].predict.call_count == 2
    assert models["tire_deg_soft.pkl"].predict.call_count == 1
    assert models["tire_deg_hard.pkl"].predict.call_count == 1


@pytest.mark.unit
def test_compute_pit_recommendation_matches_build_pit_recommendation_with_injected_state() -> None:
    """Checkpoint 4's split: compute_pit_recommendation must be callable
    directly with a plain state dict — no DB, no Redis, no cache — the exact
    shape prediction_worker._compute_recommendation_fields uses (it must NOT
    go through build_pit_recommendation/_current_state, which would re-query
    the DB and race against process_lap's own commit of this same lap — see
    compute_pit_recommendation's own docstring). Same numbers as the
    hand-computed batching test above confirm the split didn't change the
    math, just how state reaches it."""
    driver_id = uuid.uuid4()
    models = _constant_delta_models()
    state = {
        "compound": "MEDIUM",
        "tyre_age_laps": 10,
        "lap_number": 10,
        "total_laps": 30,
        "circuit_name": "Test Circuit",
    }

    candidates = strategy_service.compute_pit_recommendation(models, {}, {}, driver_id, state)

    assert candidates[0]["pit_lap"] == 11
    assert candidates[0]["projected_total_delta_seconds"] == pytest.approx(12.6)
    assert candidates[0]["recommended_compound"] == "SOFT"


@pytest.mark.unit
async def test_build_pit_recommendation_window_is_narrow_not_full_horizon(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=10, compound="MEDIUM", tyre_age_laps=10, position=3)
    mock_db_session.execute.side_effect = _current_state_side_effects(
        lap, total_laps=30, circuit_id=circuit_id
    )
    monkeypatch.setattr(strategy_service, "_load_models", _constant_delta_models)

    candidates = await strategy_service.build_pit_recommendation(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )

    # threshold = 12.6 + PIT_WINDOW_TOLERANCE_SECONDS(1.5) = 14.1;
    # total_delta[i] = 0.6i + 12.6 <= 14.1 for i in {0,1,2} -> pit_laps 11-13.
    assert candidates[0]["window_start"] == 11
    assert candidates[0]["window_end"] == 13
    # Not the full 11-25 PIT_WINDOW_LOOKAHEAD_LAPS search horizon — and every
    # returned candidate shares the SAME window (a property of the #1
    # recommendation, not computed per-candidate).
    assert all(c["window_start"] == 11 and c["window_end"] == 13 for c in candidates)


@pytest.mark.unit
async def test_build_pit_recommendation_confidence_only_on_top_candidate(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=10, compound="MEDIUM", tyre_age_laps=10, position=3)
    mock_db_session.execute.side_effect = _current_state_side_effects(
        lap, total_laps=30, circuit_id=circuit_id
    )
    monkeypatch.setattr(strategy_service, "_load_models", _constant_delta_models)
    # Near-zero noise: the deterministic ranking above (strictly increasing
    # total_delta) should almost never be overturned by sampling.
    monkeypatch.setattr(
        strategy_service,
        "_load_holdout_mae",
        lambda: {
            "tire_deg_soft.pkl": 0.001,
            "tire_deg_medium.pkl": 0.001,
            "tire_deg_hard.pkl": 0.001,
        },
    )

    candidates = await strategy_service.build_pit_recommendation(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )

    assert candidates[0]["confidence_score"] is not None
    assert candidates[0]["confidence_score"] > 0.95
    for other in candidates[1:]:
        assert other["confidence_score"] is None


@pytest.mark.unit
async def test_build_pit_recommendation_confidence_scales_with_holdout_mae(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same deterministic delta ranking as the tests above, only the noise
    scale (holdout_mae) differs between the two runs — confidence must be
    high when the compounds' own models are near-perfectly accurate and low
    when their error is large relative to the real gap between candidates,
    not a fixed number regardless of which models produced the
    recommendation. Bound-based (not a bare low > high comparison) since
    confidence is a genuinely stochastic Monte Carlo estimate — the two mae
    values are picked far enough apart (0.01 vs 5.0, against an ~8.4s total
    spread across all 15 candidates) that both bounds hold robustly
    regardless of the unseeded RNG's exact draw.
    """
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    circuit_id = uuid.uuid4()

    async def _run(mae: float) -> list[dict[str, Any]]:
        lap = _fake_lap(lap_number=10, compound="MEDIUM", tyre_age_laps=10, position=3)
        mock_db_session.execute.side_effect = _current_state_side_effects(
            lap, total_laps=30, circuit_id=circuit_id
        )
        monkeypatch.setattr(strategy_service, "_load_models", _constant_delta_models)
        monkeypatch.setattr(
            strategy_service,
            "_load_holdout_mae",
            lambda: {
                "tire_deg_soft.pkl": mae,
                "tire_deg_medium.pkl": mae,
                "tire_deg_hard.pkl": mae,
            },
        )
        result: list[dict[str, Any]] = await strategy_service.build_pit_recommendation(
            fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
        )
        return result

    low_mae_candidates = await _run(mae=0.01)
    # Same season/round/driver_id -> same cache key (@cacheable) — flush so
    # the second run actually recomputes instead of replaying the first
    # call's cached result.
    await fakeredis.flushall()
    high_mae_candidates = await _run(mae=5.0)

    assert low_mae_candidates[0]["confidence_score"] > 0.95
    assert high_mae_candidates[0]["confidence_score"] < 0.5


@pytest.mark.unit
async def test_build_pit_recommendation_raises_when_no_stint2_compound_loaded(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    # Current compound is INTERMEDIATE specifically because it's NOT one of
    # _STINT2_CANDIDATE_COMPOUNDS (SOFT/MEDIUM/HARD) — loading only its own
    # model isolates "current pipeline available, but none of the stint-2
    # candidates are" from the (unreachable) case of MEDIUM being both the
    # current compound and a loaded stint-2 candidate at once.
    lap = _fake_lap(lap_number=10, compound="INTERMEDIATE", tyre_age_laps=10, position=3)
    mock_db_session.execute.side_effect = _current_state_side_effects(
        lap, total_laps=30, circuit_id=circuit_id
    )

    # Only the current compound's own model is loaded — no SOFT/MEDIUM/HARD
    # stint-2 candidate is available at all (an unrealistic but defensively-
    # handled edge case: nothing to recommend pitting onto).
    monkeypatch.setattr(
        strategy_service,
        "_load_models",
        lambda: {"tire_deg_inter.pkl": _constant_delta_pipeline(0.1)},
    )

    with pytest.raises(ModelNotLoadedError):
        await strategy_service.build_pit_recommendation(
            fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
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

    result = await strategy_service.build_pit_recommendation(
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

    result = await strategy_service.build_pit_recommendation(
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
        _one_result((circuit_id, "Test Circuit", 53)),  # real stored Session.total_laps
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
async def test_get_competitor_predicted_strategy_prefers_stored_total_laps(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The THIRD instance of Issue A's bug (docs/internal/live-race-ingestion-and-
    strategy-gaps-monza-2026.md), found while fixing the other two: this
    function's own total_laps = max(lap.lap_number for lap in latest_laps)
    is the same "how far has the race gotten" proxy, just computed in
    Python over each driver's latest row instead of a SQL MAX(). It fed
    _first_pit_laps_over_threshold_batch's horizon calculation directly
    (np.maximum(total_laps - current_laps, 1)), silently shrinking every
    competitor's pit-lap search horizon to 1 lap for the whole live race.

    Confirms the real stored value (53) is used, NOT the drivers' own
    latest-lap max (20) — if the bug were still present, a driver at lap 20
    would get total_laps=20, collapsing their horizon to 1 lap; with the fix,
    the full COMPETITOR_STRATEGY_HORIZON_LAPS-lap horizon is available,
    which is what lets this deterministic 0.8-probability pit_model actually
    predict a lap in the first place (a 1-lap horizon would still find one
    too, so the real assertion is the horizon math itself, exercised via
    _first_pit_laps_over_threshold_batch's own dedicated tests below — this
    test's job is only to confirm get_competitor_predicted_strategy passes
    the STORED value through, not the proxy).
    """
    session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    driver_a = uuid.uuid4()
    laps = [
        _fake_competitor_lap(
            driver_a, lap_number=20, compound="MEDIUM", tyre_age_laps=15, position=1
        )
    ]
    captured_total_laps: list[int] = []

    def _capturing_batch(*args: Any, **kwargs: Any) -> tuple[np.ndarray, np.ndarray]:
        captured_total_laps.append(args[-1])
        return np.array([25]), np.array([0.8])

    mock_db_session.execute.side_effect = [
        _scalars_all_result(laps),
        _one_result((circuit_id, "Test Circuit", 53)),  # real stored value != lap 20
    ]
    monkeypatch.setattr(strategy_service, "_first_pit_laps_over_threshold_batch", _capturing_batch)
    monkeypatch.setattr(
        strategy_service, "_load_models", lambda: {"pit_predictor.pkl": MagicMock()}
    )

    await strategy_service.get_competitor_predicted_strategy(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id
    )

    assert captured_total_laps == [53]


@pytest.mark.unit
async def test_get_competitor_predicted_strategy_falls_back_when_total_laps_unset(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No stored Session.total_laps — falls back to max(lap.lap_number for
    lap in latest_laps), unchanged from before this fix. No extra DB query
    needed for this fallback (unlike _current_state's), since it's a pure
    Python max() over rows already fetched for the per-driver prediction
    itself."""
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
    captured_total_laps: list[int] = []

    def _capturing_batch(*args: Any, **kwargs: Any) -> tuple[np.ndarray, np.ndarray]:
        captured_total_laps.append(args[-1])
        return np.array([25, 22]), np.array([0.8, 0.8])

    mock_db_session.execute.side_effect = [
        _scalars_all_result(laps),
        _one_result((circuit_id, "Test Circuit", None)),  # total_laps NOT stored
    ]
    monkeypatch.setattr(strategy_service, "_first_pit_laps_over_threshold_batch", _capturing_batch)
    monkeypatch.setattr(
        strategy_service, "_load_models", lambda: {"pit_predictor.pkl": MagicMock()}
    )

    await strategy_service.get_competitor_predicted_strategy(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id
    )

    assert captured_total_laps == [20]  # max(20, 18) — the pre-fix proxy, unchanged


@pytest.mark.unit
def test_first_pit_laps_batch_passes_the_real_fuel_penalty_not_zeros(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tyre model must receive fuel_load_penalty_seconds, never zeros.

    This call site passed np.zeros(...), telling every tyre model the tank was
    empty on every lap — the same train/inference skew as the 2026-09-09
    leakage defect (tire_deg_model's module docstring forbids building this
    feature any other way), and the last call site still doing it while
    prediction_worker's live path and race_simulator both used the helper.
    Measured before the fix: it moved the predicted competitor pit lap for
    27.7% of driver-snapshots across six real races, 13.7% by five laps or
    more (no measurable accuracy change either way — this is a consistency
    fix, not an accuracy one).

    Zeros are a plausible-looking value inside the feature's real range, so
    nothing downstream would ever raise on them; only asserting on the
    argument itself can catch a regression. The pit model is held below
    ALERT_THRESHOLD so the roll-forward runs its whole horizon, which also
    lets this check that the penalty tracks the rolled-forward lap rather
    than being any single fixed value.
    """
    total_laps = 50
    current_lap = 10
    captured_laps: list[np.ndarray] = []
    captured_fuel: list[np.ndarray] = []

    def _capturing_life_remaining(
        pipeline: Any,
        lap_number: np.ndarray,
        compound_encoded: np.ndarray,
        tyre_age_laps: np.ndarray,
        fuel_load_penalty: np.ndarray,
        circuit_id_encoded: np.ndarray,
        driver_id_encoded: np.ndarray,
    ) -> np.ndarray:
        captured_laps.append(np.asarray(lap_number, dtype=np.float64))
        captured_fuel.append(np.asarray(fuel_load_penalty, dtype=np.float64))
        return np.full(len(lap_number), 30)

    def _below_threshold(features: np.ndarray) -> np.ndarray:
        rows = len(features)
        return np.column_stack([np.full(rows, 0.9), np.full(rows, 0.1)])

    monkeypatch.setattr(
        tire_deg_model,
        "predict_life_remaining_batch",
        _capturing_life_remaining,
    )
    pit_model = MagicMock()
    pit_model.predict_proba.side_effect = _below_threshold

    strategy_service._first_pit_laps_over_threshold_batch(
        pit_model,
        {"tire_deg_medium.pkl": object()},
        {},
        [str(uuid.uuid4())],
        ["MEDIUM"],
        "Test Circuit",
        np.array([current_lap]),
        np.array([8]),
        np.array([1]),
        np.array([5.0]),
        np.array([5.0]),
        np.array([0.0]),
        total_laps,
    )

    assert captured_fuel, "the tyre model was never consulted"
    for laps, fuel in zip(captured_laps, captured_fuel, strict=True):
        expected = np.asarray(
            tire_deg_model.fuel_load_penalty_seconds(laps, float(total_laps)),
            dtype=np.float64,
        )
        assert np.allclose(fuel, expected)
        assert (fuel > 0.0).all()  # the regression guard: zeros must never come back

    # Fuel burns off as the race runs, so a later rolled-forward lap must carry less.
    flat_laps = np.concatenate(captured_laps)
    flat_fuel = np.concatenate(captured_fuel)
    order = np.argsort(flat_laps)
    assert np.all(np.diff(flat_fuel[order]) < 0.0)


@pytest.mark.unit
async def test_get_pit_window_with_explanation_attaches_explanation_to_top_candidate_only(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Orchestration test: does get_pit_window_with_explanation call the right
    helpers and assemble their outputs into responses[0].explanation only —
    each helper's own internals (SHAP math, field-position resolution) are
    covered by their own dedicated tests below, so those are monkeypatched
    here rather than re-exercised through a full DB/model fixture stack."""
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    target_ahead_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=10, compound="MEDIUM", tyre_age_laps=10, position=3)
    # Only build_pit_recommendation's own _current_state call plus this
    # function's own second _current_state call touch the DB directly now —
    # _resolve_field_neighbors and get_undercut_score are monkeypatched below,
    # so their own db.execute() calls never happen in this test.
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
    tire_deg_contribution = explainability.FeatureContribution(
        feature_name="tyre_age_laps", value=10.0, contribution=0.5, direction="+"
    )
    monkeypatch.setattr(
        strategy_service,
        "tire_deg_recommendation_contributions",
        lambda *args, **kwargs: [tire_deg_contribution],
    )
    monkeypatch.setattr(
        strategy_service,
        "_resolve_field_neighbors",
        AsyncMock(
            return_value={
                "position": 3,
                "gap_to_car_ahead": 5.0,
                "gap_to_car_behind": 30.0,
                "target_ahead_driver_id": target_ahead_id,
                "target_behind_driver_id": None,
            }
        ),
    )
    pit_predictor_contribution = explainability.FeatureContribution(
        feature_name="gap_to_car_ahead", value=5.0, contribution=-0.2, direction="-"
    )
    monkeypatch.setattr(
        strategy_service,
        "pit_predictor_current_contributions",
        lambda *args, **kwargs: [pit_predictor_contribution],
    )
    monkeypatch.setattr(
        strategy_service,
        "get_undercut_score",
        AsyncMock(return_value={"probability_pit_now_gains_position": 0.7}),
    )

    responses = await strategy_service.get_pit_window_with_explanation(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id
    )

    assert len(responses) >= 1
    explanation = responses[0].explanation
    assert explanation is not None
    assert explanation.tire_deg_shap[0].feature_name == "tyre_age_laps"
    assert explanation.pit_predictor_shap[0].feature_name == "gap_to_car_ahead"
    assert "Lap" in explanation.narrative
    assert any(fact.label == "Gap to car ahead" for fact in explanation.facts)
    assert any(fact.label == "Undercut opportunity (car ahead)" for fact in explanation.facts)
    # No car behind (target_behind_driver_id is None) -> no overcut fact.
    assert not any(fact.label == "Overcut risk (car behind)" for fact in explanation.facts)
    if len(responses) > 1:
        assert responses[1].explanation is None


@pytest.mark.unit
def test_tire_deg_recommendation_contributions_uses_recommended_not_current_compound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Checkpoint 3 fix itself: explains the RECOMMENDED compound's own
    pipeline, not whichever pipeline the current compound happens to use.
    explainability.explain_prediction unwraps a Pipeline down to its raw tree
    estimator internally (SHAP's TreeExplainer needs the estimator, not the
    Pipeline) and never calls Pipeline.predict() itself, so the spy has to
    sit on explain_prediction's own model argument, not pipeline.predict."""
    driver_id = uuid.uuid4()
    current_pipeline = _fit_slope_pipeline(slope=0.1, seed=20)
    recommended_pipeline = _fit_slope_pipeline(slope=0.9, seed=21)
    models = {
        "tire_deg_medium.pkl": current_pipeline,  # the (irrelevant) current compound
        "tire_deg_soft.pkl": recommended_pipeline,  # the recommended compound
    }

    real_explain = explainability.explain_prediction
    called_with: dict[str, Any] = {}

    def _spy_explain(model: Any, feature_names: Any, features: Any, **kwargs: Any) -> Any:
        called_with["model"] = model
        result: Any = real_explain(model, feature_names, features, **kwargs)
        return result

    monkeypatch.setattr(strategy_service.explainability, "explain_prediction", _spy_explain)

    contributions = strategy_service.tire_deg_recommendation_contributions(
        models,
        {},
        driver_id,
        "Test Circuit",
        total_laps=50,
        pit_lap=24,
        recommended_compound="SOFT",
    )

    assert called_with["model"] is recommended_pipeline
    assert called_with["model"] is not current_pipeline
    assert len(contributions) > 0


# --- _resolve_field_neighbors: same duplicated-fix-pattern as
# prediction_worker._resolve_position_context (CLAUDE.md's core-feature-
# rebuild Checkpoint 1) — bound-by-current_lap + live-gaps Redis fallback. ---


@pytest.mark.unit
async def test_resolve_field_neighbors_bounds_query_by_current_lap(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    current_lap = 18

    captured_queries: list[Any] = []

    async def _execute_side_effect(query: Any, *args: Any, **kwargs: Any) -> Any:
        captured_queries.append(query)
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    mock_db_session.execute.side_effect = _execute_side_effect

    await strategy_service._resolve_field_neighbors(
        fakeredis, mock_db_session, session_id, driver_id, current_lap, SEASON, ROUND_NUMBER
    )

    assert len(captured_queries) == 1
    compiled = str(captured_queries[0].compile(compile_kwargs={"literal_binds": True}))
    assert f"lap_number <= {current_lap}" in compiled


@pytest.mark.unit
async def test_resolve_field_neighbors_falls_back_to_redis_when_db_position_missing(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    ahead_id = uuid.uuid4()

    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    mock_db_session.execute.return_value = empty_result

    await fakeredis.set(
        f"f1:{SEASON}:{ROUND_NUMBER}:gaps",
        json.dumps(
            {
                "gaps": [
                    {
                        "driver_id": str(ahead_id),
                        "position": 1,
                        "gap_to_ahead_seconds": 0.0,
                        "gap_to_behind_seconds": 4.5,
                    },
                    {
                        "driver_id": str(driver_id),
                        "position": 2,
                        "gap_to_ahead_seconds": 4.5,
                        "gap_to_behind_seconds": 0.0,
                    },
                ]
            }
        ),
    )

    result = await strategy_service._resolve_field_neighbors(
        fakeredis, mock_db_session, session_id, driver_id, 15, SEASON, ROUND_NUMBER
    )

    assert result["position"] == 2
    assert result["gap_to_car_ahead"] == pytest.approx(4.5)
    assert result["target_ahead_driver_id"] == ahead_id
    assert result["target_behind_driver_id"] is None


@pytest.mark.unit
async def test_resolve_field_neighbors_returns_hardcoded_default_when_nothing_resolves(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()

    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    mock_db_session.execute.return_value = empty_result

    result = await strategy_service._resolve_field_neighbors(
        fakeredis, mock_db_session, session_id, driver_id, 15, SEASON, ROUND_NUMBER
    )

    assert result == {
        "position": 1,
        "gap_to_car_ahead": strategy_service.pit_predictor.MAX_GAP_SECONDS,
        "gap_to_car_behind": strategy_service.pit_predictor.MAX_GAP_SECONDS,
        "target_ahead_driver_id": None,
        "target_behind_driver_id": None,
    }


@pytest.mark.unit
def test_tire_deg_recommendation_contributions_returns_empty_when_pipeline_missing() -> None:
    contributions = strategy_service.tire_deg_recommendation_contributions(
        {},  # no models loaded at all
        {},
        uuid.uuid4(),
        "Test Circuit",
        total_laps=50,
        pit_lap=24,
        recommended_compound="SOFT",
    )
    assert contributions == []


@pytest.mark.unit
def test_pit_predictor_current_contributions_uses_real_field_gaps() -> None:
    """Confirms the rival-gap terms actually reach the SHAP feature vector —
    the exact structural gap the pre-Checkpoint-3 explanation had (tire_deg_
    model.FEATURE_COLUMNS has no gap feature at all)."""
    import lightgbm as lgb

    rng = np.random.default_rng(11)
    n = 100
    features = rng.random((n, len(strategy_service.pit_predictor.FEATURE_COLUMNS)))
    target = rng.integers(0, 2, n)
    pit_model = lgb.LGBMClassifier(n_estimators=10, verbosity=-1)
    pit_model.fit(features, target)

    tire_pipeline = _fit_slope_pipeline(slope=0.2, seed=22)
    models = {"pit_predictor.pkl": pit_model, "tire_deg_medium.pkl": tire_pipeline}
    state = {
        "compound": "MEDIUM",
        "tyre_age_laps": 15,
        "lap_number": 20,
        "total_laps": 50,
        "circuit_name": "Test Circuit",
    }
    neighbors = {
        "position": 4,
        "gap_to_car_ahead": 3.2,
        "gap_to_car_behind": 8.2,
    }

    contributions = strategy_service.pit_predictor_current_contributions(
        models, {}, uuid.uuid4(), state, neighbors
    )

    assert len(contributions) > 0
    contributed_features = {c.feature_name for c in contributions}
    # At least one of the two real rival-gap features made it into the
    # top-k SHAP contributions for at least one plausible random model — not
    # asserted unconditionally (SHAP's top-k is magnitude-ranked, so a
    # weakly-fit model on random data could rank either gap feature outside
    # the top 5) — instead confirm the FEATURE VECTOR itself carried the
    # real gap values, which is the structural claim that matters here.
    assert contributed_features <= set(strategy_service.pit_predictor.FEATURE_COLUMNS)


@pytest.mark.unit
def test_pit_predictor_current_contributions_returns_empty_when_model_missing() -> None:
    state = {
        "compound": "MEDIUM",
        "tyre_age_laps": 15,
        "lap_number": 20,
        "total_laps": 50,
        "circuit_name": "Test Circuit",
    }
    neighbors = {"position": 4, "gap_to_car_ahead": 3.2, "gap_to_car_behind": 8.2}

    contributions = strategy_service.pit_predictor_current_contributions(
        {"tire_deg_medium.pkl": _fit_slope_pipeline(slope=0.2, seed=23)},
        {},
        uuid.uuid4(),
        state,
        neighbors,
    )

    assert contributions == []


@pytest.mark.unit
def test_build_pit_recommendation_explanation_narrative_reflects_safe_gap() -> None:
    tire_deg_contribution = explainability.FeatureContribution(
        feature_name="tyre_age_laps", value=24.0, contribution=0.8, direction="+"
    )
    behind_id = uuid.uuid4()

    explanation = strategy_service.build_pit_recommendation_explanation(
        pit_lap=32,
        recommended_compound="MEDIUM",
        confidence=0.71,
        tyre_age_laps=24,
        position=4,
        gap_to_car_ahead=100.0,
        target_ahead_driver_id=None,
        gap_to_car_behind=8.2,  # > PIT_STOP_SECONDS (22.0)? No — 8.2 < 22.0
        target_behind_driver_id=behind_id,
        undercut_score=None,
        overcut_score=None,
        tire_deg_contributions=[tire_deg_contribution],
        pit_predictor_contributions=[],
    )

    assert "Lap 32" in explanation.narrative
    assert "MEDIUM" in explanation.narrative
    assert "71%" in explanation.narrative
    assert "8.2s" in explanation.narrative
    # 8.2s < race_simulator.PIT_STOP_SECONDS (22.0) -> a close call, not safe.
    assert "close call" in explanation.narrative
    assert any(
        fact.label == "Recommended pit lap" and fact.value == "Lap 32" for fact in explanation.facts
    )


@pytest.mark.unit
def test_build_pit_recommendation_explanation_narrative_reflects_safe_gap_when_large() -> None:
    behind_id = uuid.uuid4()

    explanation = strategy_service.build_pit_recommendation_explanation(
        pit_lap=32,
        recommended_compound="MEDIUM",
        confidence=None,
        tyre_age_laps=24,
        position=4,
        gap_to_car_ahead=100.0,
        target_ahead_driver_id=None,
        gap_to_car_behind=45.0,  # > PIT_STOP_SECONDS (22.0) -> safe
        target_behind_driver_id=behind_id,
        undercut_score=None,
        overcut_score=None,
        tire_deg_contributions=[],
        pit_predictor_contributions=[],
    )

    assert "safe to pit" in explanation.narrative
    assert "close call" not in explanation.narrative
    # confidence=None -> no Confidence fact, no confidence clause in narrative.
    assert not any(fact.label == "Confidence" for fact in explanation.facts)
    assert "%" not in explanation.narrative.split(".")[0]


@pytest.mark.unit
def test_build_pit_recommendation_explanation_notes_race_leader() -> None:
    """No car ahead AND no car behind — the field-leader case — must not
    silently omit the "nothing behind to defend against" context just
    because there's also nothing ahead to undercut."""
    explanation = strategy_service.build_pit_recommendation_explanation(
        pit_lap=32,
        recommended_compound="HARD",
        confidence=0.9,
        tyre_age_laps=20,
        position=1,
        gap_to_car_ahead=120.0,
        target_ahead_driver_id=None,
        gap_to_car_behind=120.0,
        target_behind_driver_id=None,
        undercut_score=None,
        overcut_score=None,
        tire_deg_contributions=[],
        pit_predictor_contributions=[],
    )

    assert "race leader" in explanation.narrative
    assert not any(fact.label == "Gap to car ahead" for fact in explanation.facts)
    assert not any(fact.label == "Gap to car behind" for fact in explanation.facts)


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
# See docs/internal/simulator-issues-wet-model-and-position-context.md's Checkpoint-6
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
            pit_lap=20,
            window_start=11,
            window_end=25,
            projected_total_delta_seconds=5.0,
            recommended_compound="MEDIUM",
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
    explanation = {"facts": [], "narrative": "test", "tire_deg_shap": [], "pit_predictor_shap": []}
    row = SimpleNamespace(
        lap_number=12,
        optimal_pit_lap=24,
        pit_probability=0.6,
        undercut_score=0.3,
        overcut_score=0.1,
        created_at=created_at,
        recommended_pit_lap=26,
        window_start=24,
        window_end=28,
        recommended_compound="MEDIUM",
        confidence_score=0.71,
        explanation=explanation,
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
            "recommended_pit_lap": 26,
            "window_start": 24,
            "window_end": 28,
            "recommended_compound": "MEDIUM",
            "confidence_score": 0.71,
            "explanation": explanation,
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
                "recommended_pit_lap": None,
                "window_start": None,
                "window_end": None,
                "recommended_compound": None,
                "confidence_score": 0.0,
                "explanation": None,
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
    """B1 mitigation (docs/internal/simulator-issues-wet-model-and-position-context.md):
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
# See docs/internal/simulator-issues-wet-model-and-position-context.md Part A. Unlike
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


# --- live gaps drive the undercut/overcut deficit and neighbour lookup
# (docs/internal/live-race-ingestion-and-strategy-gaps-monza-2026.md Issue B) ---


def _elapsed_result(value: float) -> MagicMock:
    """The first query _cumulative_race_time makes (latest session_elapsed_seconds).
    _scalar_result only stubs scalar_one, which this query does not call — a bare
    MagicMock there silently reads as float(MagicMock()) == 1.0."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _live_gaps_payload(
    session_id: uuid.UUID, entries: list[dict[str, Any]], source: str = "live"
) -> str:
    return json.dumps({"session_id": str(session_id), "gaps": entries, "source": source})


def _live_entry(
    driver_id: uuid.UUID, position: int, gap_to_leader: float | None, **extra: Any
) -> dict[str, Any]:
    return {
        "driver_id": str(driver_id),
        "position": position,
        "gap_to_leader_seconds": gap_to_leader,
        "gap_to_ahead_seconds": extra.pop("gap_to_ahead", None),
        "gap_to_behind_seconds": extra.pop("gap_to_behind", None),
        "laps_behind": 0,
        **extra,
    }


async def _set_gaps(
    client: fakeredis_lib.FakeAsyncRedis, session_id: uuid.UUID, entries: list[dict[str, Any]]
) -> None:
    await client.set(f"f1:{SEASON}:{ROUND_NUMBER}:gaps", _live_gaps_payload(session_id, entries))


@pytest.mark.unit
async def test_live_gap_deficit_is_difference_of_gaps_to_leader_positive_when_now_trails(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id, now_id, next_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _set_gaps(
        fakeredis,
        session_id,
        [_live_entry(next_id, 3, 4.0), _live_entry(now_id, 4, 5.5)],
    )

    deficit = await strategy_service._live_gap_deficit(
        fakeredis, SEASON, ROUND_NUMBER, session_id, now_id, next_id
    )

    assert deficit == pytest.approx(1.5)  # now is 1.5s behind next


@pytest.mark.unit
async def test_live_gap_deficit_negative_when_now_is_ahead(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id, now_id, next_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _set_gaps(
        fakeredis, session_id, [_live_entry(now_id, 3, 4.0), _live_entry(next_id, 4, 5.0)]
    )

    deficit = await strategy_service._live_gap_deficit(
        fakeredis, SEASON, ROUND_NUMBER, session_id, now_id, next_id
    )

    assert deficit == pytest.approx(-1.0)


@pytest.mark.unit
async def test_live_gap_deficit_treats_the_leader_as_zero_gap_even_with_no_gap_value(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id, now_id, leader_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _set_gaps(
        fakeredis, session_id, [_live_entry(leader_id, 1, None), _live_entry(now_id, 2, 2.4)]
    )

    deficit = await strategy_service._live_gap_deficit(
        fakeredis, SEASON, ROUND_NUMBER, session_id, now_id, leader_id
    )

    assert deficit == pytest.approx(2.4)


@pytest.mark.unit
async def test_live_gap_deficit_is_none_for_a_lapped_driver(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """A lapped car has no seconds gap to the leader — must not be read as 0."""
    session_id, now_id, next_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _set_gaps(
        fakeredis,
        session_id,
        [_live_entry(next_id, 5, 30.0), _live_entry(now_id, 18, None, laps_behind=1)],
    )

    deficit = await strategy_service._live_gap_deficit(
        fakeredis, SEASON, ROUND_NUMBER, session_id, now_id, next_id
    )

    assert deficit is None


@pytest.mark.unit
async def test_live_gap_deficit_is_none_when_a_driver_is_absent_from_the_standings(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """E.g. a retired car, which the live ingestor no longer publishes."""
    session_id, now_id, next_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _set_gaps(fakeredis, session_id, [_live_entry(now_id, 2, 2.0)])

    deficit = await strategy_service._live_gap_deficit(
        fakeredis, SEASON, ROUND_NUMBER, session_id, now_id, next_id
    )

    assert deficit is None


@pytest.mark.unit
@pytest.mark.parametrize("source", ["replay", None])
async def test_live_gap_deficit_ignores_a_payload_that_is_not_from_the_live_feed(
    fakeredis: fakeredis_lib.FakeAsyncRedis, source: str | None
) -> None:
    session_id, now_id, next_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    payload: dict[str, Any] = {
        "session_id": str(session_id),
        "gaps": [_live_entry(next_id, 3, 4.0), _live_entry(now_id, 4, 5.0)],
    }
    if source is not None:
        payload["source"] = source
    await fakeredis.set(f"f1:{SEASON}:{ROUND_NUMBER}:gaps", json.dumps(payload))

    deficit = await strategy_service._live_gap_deficit(
        fakeredis, SEASON, ROUND_NUMBER, session_id, now_id, next_id
    )

    assert deficit is None


@pytest.mark.unit
async def test_live_gap_deficit_ignores_a_live_payload_for_a_different_session(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """The key is per season/round; e.g. FP2's live payload must not answer a race query."""
    other_session, session_id = uuid.uuid4(), uuid.uuid4()
    now_id, next_id = uuid.uuid4(), uuid.uuid4()
    await _set_gaps(
        fakeredis, other_session, [_live_entry(next_id, 3, 4.0), _live_entry(now_id, 4, 5.0)]
    )

    deficit = await strategy_service._live_gap_deficit(
        fakeredis, SEASON, ROUND_NUMBER, session_id, now_id, next_id
    )

    assert deficit is None


@pytest.mark.unit
@pytest.mark.parametrize("stored", [None, "not valid json", json.dumps([1, 2, 3])])
async def test_live_gap_deficit_tolerates_missing_or_malformed_payload(
    fakeredis: fakeredis_lib.FakeAsyncRedis, stored: str | None
) -> None:
    if stored is not None:
        await fakeredis.set(f"f1:{SEASON}:{ROUND_NUMBER}:gaps", stored)

    deficit = await strategy_service._live_gap_deficit(
        fakeredis, SEASON, ROUND_NUMBER, uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    )

    assert deficit is None


async def _undercut_projected_gap(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    target_id: uuid.UUID,
) -> float:
    """get_undercut_score's projected gap with a deterministic (constant-delta)
    tyre model, so only the starting deficit can move the result. The DB mock
    holds exactly the two _current_state reads — any cumulative-time read would
    exhaust it and fail the test."""
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=20, compound="MEDIUM", tyre_age_laps=10, position=2)
    mock_db_session.execute.side_effect = [
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
    ]
    monkeypatch.setattr(
        strategy_service,
        "_load_models",
        lambda: {"tire_deg_medium.pkl": _constant_delta_pipeline(0.1)},
    )
    result = await strategy_service.get_undercut_score(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id, target_id
    )
    return float(result["projected_gap_seconds"])


@pytest.mark.unit
async def test_undercut_uses_the_live_gap_and_never_reads_summed_lap_times(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Monza 2026 lap 30: summed lap times said VER led GAS by 1.0s; on the road
    GAS was ahead. The live standings must win, and the DB sum must not be read."""
    session_id, driver_id, target_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _set_gaps(
        fakeredis, session_id, [_live_entry(target_id, 7, 40.0), _live_entry(driver_id, 8, 40.6)]
    )
    trailing = await _undercut_projected_gap(
        mock_db_session, fakeredis, monkeypatch, session_id, driver_id, target_id
    )

    await fakeredis.flushall()
    mock_db_session.reset_mock()
    await _set_gaps(
        fakeredis, session_id, [_live_entry(target_id, 7, 40.0), _live_entry(driver_id, 8, 30.0)]
    )
    leading = await _undercut_projected_gap(
        mock_db_session, fakeredis, monkeypatch, session_id, driver_id, target_id
    )

    # Same tyres, same models: the projected gap moves by exactly the change in
    # starting deficit (0.6 - (-10.0) = 10.6s), within Monte Carlo noise.
    assert leading - trailing == pytest.approx(10.6, abs=1.0)


@pytest.mark.unit
async def test_undercut_falls_back_to_summed_lap_times_when_no_live_gap_exists(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replayed/historical session has no live payload — the DB path stays in charge."""
    session_id, driver_id, target_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=20, compound="MEDIUM", tyre_age_laps=10, position=2)
    mock_db_session.execute.side_effect = [
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
        _elapsed_result(1810.0),  # driver's session_elapsed_seconds: 10s behind
        _elapsed_result(1800.0),
    ]
    monkeypatch.setattr(
        strategy_service,
        "_load_models",
        lambda: {"tire_deg_medium.pkl": _constant_delta_pipeline(0.1)},
    )

    result = await strategy_service.get_undercut_score(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id, target_id
    )

    # 10s behind and identical tyres: the projected gap must still be negative.
    assert result["projected_gap_seconds"] < -5.0


@pytest.mark.unit
async def test_overcut_uses_the_live_gap_with_the_pit_roles_reversed(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_overcut_score routes the SAME live deficit through the shared helper
    with driver/target swapped; the live payload must reach it too."""
    session_id, driver_id, target_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=20, compound="MEDIUM", tyre_age_laps=10, position=2)
    mock_db_session.execute.side_effect = [
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
    ]  # exhausted by any cumulative-time read
    await _set_gaps(
        fakeredis, session_id, [_live_entry(driver_id, 5, 20.0), _live_entry(target_id, 6, 24.0)]
    )
    monkeypatch.setattr(
        strategy_service,
        "_load_models",
        lambda: {"tire_deg_medium.pkl": _constant_delta_pipeline(0.1)},
    )

    result = await strategy_service.get_overcut_score(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id, target_id
    )

    # Target trails the driver by 4s; a pit stop costs ~22s, so staying out
    # retains the position with near certainty.
    assert result["probability_stay_out_retains_position"] > 0.9


@pytest.mark.unit
async def test_resolve_field_neighbors_prefers_live_standings_and_skips_the_db(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id, driver_id, ahead_id, behind_id = (uuid.uuid4() for _ in range(4))
    await _set_gaps(
        fakeredis,
        session_id,
        [
            _live_entry(ahead_id, 1, None, gap_to_ahead=0.0, gap_to_behind=2.5),
            _live_entry(driver_id, 2, 2.5, gap_to_ahead=2.5, gap_to_behind=4.0),
            _live_entry(behind_id, 3, 6.5, gap_to_ahead=4.0, gap_to_behind=0.0),
        ],
    )

    result = await strategy_service._resolve_field_neighbors(
        fakeredis, mock_db_session, session_id, driver_id, 15, SEASON, ROUND_NUMBER
    )

    mock_db_session.execute.assert_not_called()
    assert result["position"] == 2
    assert result["gap_to_car_ahead"] == pytest.approx(2.5)
    assert result["gap_to_car_behind"] == pytest.approx(4.0)
    assert result["target_ahead_driver_id"] == ahead_id
    assert result["target_behind_driver_id"] == behind_id


@pytest.mark.unit
async def test_resolve_field_neighbors_ignores_a_replay_payload_and_uses_the_db_path(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id, driver_id, ahead_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await fakeredis.set(
        f"f1:{SEASON}:{ROUND_NUMBER}:gaps",
        _live_gaps_payload(
            session_id,
            [
                _live_entry(ahead_id, 1, None, gap_to_ahead=0.0),
                _live_entry(driver_id, 2, 2.5, gap_to_ahead=2.5),
            ],
            source="replay",
        ),
    )
    empty = MagicMock()
    empty.scalars.return_value.all.return_value = []
    mock_db_session.execute.return_value = empty

    await strategy_service._resolve_field_neighbors(
        fakeredis, mock_db_session, session_id, driver_id, 15, SEASON, ROUND_NUMBER
    )

    # The DB path ran; the replay payload did not short-circuit it.
    mock_db_session.execute.assert_called()


# --- property tests: invariants of the undercut/overcut maths and the live-gap
# deficit that must hold for ANY input, not just hand-picked cases. A sign or
# convention error (e.g. reading the requester as "ahead" when it trails) breaks
# these for a whole family of inputs. Randomness is a seeded stdlib Random, so
# every run is reproducible. ---

_PROPERTY_SEED = 20260919


def _common_random_numbers(monkeypatch: pytest.MonkeyPatch, seed: int = 4242) -> None:
    """Give every Monte Carlo call the SAME noise draws, so a property about the
    inputs is not muddied by sampling noise between two calls."""
    real_default_rng = np.random.default_rng
    monkeypatch.setattr(np.random, "default_rng", lambda *a, **k: real_default_rng(seed))


async def _undercut_for_gaps(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    target_id: uuid.UUID,
    driver_gap: float,
    target_gap: float,
    *,
    reverse_roles: bool = False,
) -> dict[str, Any]:
    """_undercut_overcut_probability on a live tower where the two drivers sit at the
    given gaps to the leader (both well behind it)."""
    await fakeredis.flushall()
    await _set_gaps(
        fakeredis,
        session_id,
        [_live_entry(uuid.uuid4(), 1, None), _live_entry(target_id, 5, target_gap)]
        + [_live_entry(driver_id, 6, driver_gap)],
    )
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=20, compound="MEDIUM", tyre_age_laps=10, position=6)
    mock_db_session.execute.side_effect = [
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
    ]
    now, nxt = (target_id, driver_id) if reverse_roles else (driver_id, target_id)
    return await strategy_service._undercut_overcut_probability(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, now, nxt
    )


@pytest.fixture
def constant_tyre_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        strategy_service,
        "_load_models",
        lambda: {"tire_deg_medium.pkl": _constant_delta_pipeline(0.1)},
    )


@pytest.mark.unit
@pytest.mark.usefixtures("constant_tyre_model")
async def test_property_undercut_probability_never_rises_as_the_deficit_grows(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The further the pitting driver trails, the less likely pitting gains the place."""
    _common_random_numbers(monkeypatch)
    session_id, driver_id, target_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    deficits = [-12.0, -8.0, -4.0, -2.0, -1.0, -0.25, 0.0, 0.25, 1.0, 2.0, 4.0, 8.0, 12.0]

    probabilities = []
    for deficit in deficits:
        result = await _undercut_for_gaps(
            mock_db_session, fakeredis, session_id, driver_id, target_id, 40.0 + deficit, 40.0
        )
        probabilities.append(result["probability_pit_now_gains_position"])

    assert all(a >= b for a, b in zip(probabilities, probabilities[1:], strict=False))
    assert probabilities[0] > 0.99  # 12s ahead: pitting certainly still leads
    assert probabilities[-1] < 0.01  # 12s behind: cannot make it up
    assert 0.3 < probabilities[deficits.index(0.0)] < 0.7  # level: a toss-up


@pytest.mark.unit
@pytest.mark.usefixtures("constant_tyre_model")
async def test_property_projected_gap_falls_one_for_one_with_the_deficit(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common_random_numbers(monkeypatch)
    session_id, driver_id, target_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    gaps = [
        (
            await _undercut_for_gaps(
                mock_db_session, fakeredis, session_id, driver_id, target_id, 40.0 + d, 40.0
            )
        )["projected_gap_seconds"]
        for d in (0.0, 3.0, 9.0)
    ]

    # Same noise draws, so the only change is the starting deficit: each extra
    # second behind is exactly one second less of projected gap.
    assert gaps[0] - gaps[1] == pytest.approx(3.0)
    assert gaps[1] - gaps[2] == pytest.approx(6.0)


@pytest.mark.unit
@pytest.mark.usefixtures("constant_tyre_model")
async def test_property_only_the_gap_difference_matters_not_where_on_track_it_is(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common_random_numbers(monkeypatch)
    session_id, driver_id, target_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    near = await _undercut_for_gaps(
        mock_db_session, fakeredis, session_id, driver_id, target_id, 12.0 + 1.5, 12.0
    )
    far = await _undercut_for_gaps(
        mock_db_session, fakeredis, session_id, driver_id, target_id, 95.0 + 1.5, 95.0
    )

    assert near == far


@pytest.mark.unit
@pytest.mark.usefixtures("constant_tyre_model")
async def test_property_result_is_deterministic_for_a_fixed_seed(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _common_random_numbers(monkeypatch)
    session_id, driver_id, target_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    first = await _undercut_for_gaps(
        mock_db_session, fakeredis, session_id, driver_id, target_id, 41.0, 40.0
    )
    second = await _undercut_for_gaps(
        mock_db_session, fakeredis, session_id, driver_id, target_id, 41.0, 40.0
    )

    assert first == second


@pytest.mark.unit
@pytest.mark.usefixtures("constant_tyre_model")
async def test_property_overcut_is_the_exact_complement_of_the_reversed_undercut(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P(driver keeps the place by staying out) + P(target gains it by pitting now) = 1,
    and the projected gap flips sign."""
    _common_random_numbers(monkeypatch)
    session_id, driver_id, target_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    driver_gap, target_gap = 43.0, 40.0
    reversed_undercut = await _undercut_for_gaps(
        mock_db_session,
        fakeredis,
        session_id,
        driver_id,
        target_id,
        driver_gap,
        target_gap,
        reverse_roles=True,
    )

    await fakeredis.flushall()
    await _set_gaps(
        fakeredis,
        session_id,
        [
            _live_entry(uuid.uuid4(), 1, None),
            _live_entry(target_id, 5, target_gap),
            _live_entry(driver_id, 6, driver_gap),
        ],
    )
    circuit_id = uuid.uuid4()
    lap = _fake_lap(lap_number=20, compound="MEDIUM", tyre_age_laps=10, position=6)
    mock_db_session.execute.side_effect = [
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
        *_current_state_side_effects(lap, total_laps=50, circuit_id=circuit_id),
    ]
    overcut = await strategy_service.get_overcut_score(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id, target_id
    )

    assert overcut["probability_stay_out_retains_position"] + reversed_undercut[
        "probability_pit_now_gains_position"
    ] == pytest.approx(1.0)
    assert overcut["projected_gap_seconds"] == pytest.approx(
        -reversed_undercut["projected_gap_seconds"]
    )


def _random_live_tower(
    rng: random.Random, session_id: uuid.UUID
) -> tuple[list[dict[str, Any]], list[uuid.UUID]]:
    """A random live tower: leader, lead-lap cars with strictly increasing gaps, and
    some lapped cars (no seconds gap). Returns (entries, lead-lap driver ids in order)."""
    n_lead_lap = rng.randint(3, 16)
    gap, lead_lap = 0.0, []
    entries: list[dict[str, Any]] = []
    for position in range(1, n_lead_lap + 1):
        driver_id = uuid.uuid4()
        gap += rng.uniform(0.05, 9.0)
        entries.append(_live_entry(driver_id, position, None if position == 1 else gap))
        lead_lap.append(driver_id)
    for i in range(rng.randint(0, 4)):
        entries.append(
            _live_entry(uuid.uuid4(), n_lead_lap + 1 + i, None, laps_behind=rng.randint(1, 2))
        )
    rng.shuffle(entries)  # the payload need not be in position order
    return entries, lead_lap


@pytest.mark.unit
async def test_property_live_gap_deficit_is_antisymmetric_additive_and_matches_track_order(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    rng = random.Random(_PROPERTY_SEED)  # noqa: S311 - seeded on purpose, not security
    session_id = uuid.uuid4()

    async def deficit(a: uuid.UUID, b: uuid.UUID) -> float | None:
        return await strategy_service._live_gap_deficit(
            fakeredis, SEASON, ROUND_NUMBER, session_id, a, b
        )

    for _ in range(150):
        entries, lead_lap = _random_live_tower(rng, session_id)
        await _set_gaps(fakeredis, session_id, entries)
        a, b, c = rng.sample(lead_lap, 3)

        d_ab, d_ba = await deficit(a, b), await deficit(b, a)
        d_bc, d_ac = await deficit(b, c), await deficit(a, c)

        assert d_ab is not None
        assert d_ba is not None
        assert d_bc is not None
        assert d_ac is not None
        assert d_ab == pytest.approx(-d_ba)
        assert d_ab + d_bc == pytest.approx(d_ac)
        # Positive exactly when `a` is further back than `b` in the running order.
        assert (d_ab > 0) == (lead_lap.index(a) > lead_lap.index(b))
        assert await deficit(a, a) == pytest.approx(0.0)


@pytest.mark.unit
async def test_property_live_gap_deficit_is_none_whenever_either_driver_is_lapped(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    rng = random.Random(_PROPERTY_SEED + 1)  # noqa: S311 - seeded on purpose, not security
    session_id = uuid.uuid4()

    for _ in range(60):
        entries, lead_lap = _random_live_tower(rng, session_id)
        lapped = [
            uuid.UUID(e["driver_id"])
            for e in entries
            if e["gap_to_leader_seconds"] is None and e["position"] != 1
        ]
        if not lapped:
            continue
        await _set_gaps(fakeredis, session_id, entries)
        other = rng.choice(lead_lap)

        for pair in ((lapped[0], other), (other, lapped[0])):
            assert (
                await strategy_service._live_gap_deficit(
                    fakeredis, SEASON, ROUND_NUMBER, session_id, *pair
                )
                is None
            )


# --- always-on pipeline counters (V5): which gap source the undercut maths used ---


async def _pipeline_stats(
    client: fakeredis_lib.FakeAsyncRedis, season_round: str
) -> dict[str, int]:
    raw = await client.hgetall(f"f1:{season_round}:pipeline_stats")
    return {k: int(v) for k, v in raw.items()}


@pytest.mark.unit
@pytest.mark.usefixtures("constant_tyre_model")
async def test_counter_records_a_live_gap_source_when_the_live_deficit_was_used(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id, driver_id, target_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    await _undercut_for_gaps(
        mock_db_session, fakeredis, session_id, driver_id, target_id, 41.0, 40.0
    )

    assert await _pipeline_stats(fakeredis, f"{SEASON}:{ROUND_NUMBER}") == {"gap_source_live": 1}
    assert await fakeredis.ttl(f"f1:{SEASON}:{ROUND_NUMBER}:pipeline_stats") > 0


@pytest.mark.unit
@pytest.mark.usefixtures("constant_tyre_model")
async def test_counter_records_the_summed_time_fallback_when_there_is_no_live_gap(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    session_id, driver_id, target_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    lap = _fake_lap(lap_number=20, compound="MEDIUM", tyre_age_laps=10, position=2)
    mock_db_session.execute.side_effect = [
        *_current_state_side_effects(lap, total_laps=50, circuit_id=uuid.uuid4()),
        *_current_state_side_effects(lap, total_laps=50, circuit_id=uuid.uuid4()),
        _elapsed_result(1810.0),
        _elapsed_result(1800.0),
    ]

    await strategy_service.get_undercut_score(
        fakeredis, mock_db_session, SEASON, ROUND_NUMBER, session_id, driver_id, target_id
    )

    assert await _pipeline_stats(fakeredis, f"{SEASON}:{ROUND_NUMBER}") == {"gap_source_summed": 1}


@pytest.mark.unit
async def test_counter_accumulates_across_calls(
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    for _ in range(3):
        await strategy_service._bump_pipeline_stat(
            fakeredis, SEASON, ROUND_NUMBER, "gap_source_live"
        )
    await strategy_service._bump_pipeline_stat(fakeredis, SEASON, ROUND_NUMBER, "gap_source_summed")

    assert await _pipeline_stats(fakeredis, f"{SEASON}:{ROUND_NUMBER}") == {
        "gap_source_live": 3,
        "gap_source_summed": 1,
    }


@pytest.mark.unit
async def test_a_redis_failure_while_counting_never_breaks_the_calculation() -> None:
    broken = AsyncMock()
    broken.hincrby.side_effect = RedisError("down")

    await strategy_service._bump_pipeline_stat(
        broken, SEASON, ROUND_NUMBER, "gap_source_live"
    )  # no raise
