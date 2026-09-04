"""Day 6 verification: a mock lap-completion event flows through Celery into Postgres.

Mirrors what ingest_live_session.py does on a real lap-completion event —
dispatches process_lap (telemetry_queue) and run_strategy_prediction
(prediction_queue) with the same raw lap dict. ML models are mocked; per the
Day 6 spec, they don't exist until Day 7 — placeholder scores are expected.

test_live_prediction_pipeline_populates_recommendation_fields (Checkpoint 4,
core-feature-rebuild) is the exception — it uses REAL fitted models (SHAP's
TreeExplainer needs an actual tree estimator, not a MagicMock, same reason
test_strategy_endpoint.py's pit-window test does the same) to verify the
new recommendation-engine columns actually get populated end-to-end through
the real Celery dispatch, not just that a degraded-gracefully None doesn't
crash the worker (the mocked-model test above already covers that).
"""

import asyncio
import uuid
from datetime import date
from unittest.mock import MagicMock

import lightgbm as lgb
import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData
from backend.services.ml import pit_predictor as pit_predictor_module
from backend.services.ml.tire_deg_model import FEATURE_COLUMNS, _build_pipeline
from backend.workers import prediction_worker
from backend.workers.celery_app import app as celery_app
from backend.workers.prediction_worker import run_strategy_prediction
from backend.workers.telemetry_worker import process_lap


@pytest.fixture(autouse=True)
def _eager_celery() -> None:
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True


@pytest.fixture
def _stub_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the real ML models — they don't exist until Day 7.

    predict.side_effect returns one prediction per input row rather than a
    fixed-length list — _run_inference now calls .predict() both directly
    (one row) and via tire_deg_model.predict_life_remaining_batch (one row
    per MAX_LOOKAHEAD_LAPS offset), and the batch call's .reshape() requires
    the returned array to match the row count it was actually called with.
    """
    stub_model = MagicMock()
    stub_model.predict.side_effect = lambda features: np.full(len(features), 2.5)
    stub_model.predict_proba.return_value = [[0.2, 0.8]]
    stub_model.probability_within.return_value = 0.05

    stub_registry = dict.fromkeys(prediction_worker._MODEL_FILES, stub_model)
    monkeypatch.setattr(prediction_worker, "_load_models", lambda: stub_registry)


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_models")
def test_mock_lap_completion_creates_strategy_prediction(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Plain (sync) test function: Celery's eager mode runs process_lap /
    # run_strategy_prediction in-process, and each task calls asyncio.run()
    # internally — exactly as it would in a real Celery worker process, which
    # never has an event loop already running in its thread. An `async def`
    # test here would put one there, and asyncio.run() would fail nested.
    circuit = Circuit(id=uuid.uuid4(), name="Test Circuit", country="Testland", track_length_km=5.0)
    race = Race(
        id=uuid.uuid4(),
        season=2025,
        round_number=1,
        circuit_id=circuit.id,
        race_date=date(2025, 3, 1),
        status="in_progress",
    )
    session_row = SessionModel(
        id=uuid.uuid4(), race_id=race.id, session_type="R", session_date=date(2025, 3, 1)
    )
    driver = Driver(id=uuid.uuid4(), code="VER", full_name="Max Verstappen", nationality="NED")

    async def _seed() -> None:
        async with db_session_factory() as db:
            db.add_all([circuit, race, session_row, driver])
            await db.commit()
        # Dispose before the next separately-asyncio.run()'d unit of work —
        # see db_session_factory's docstring for why.
        await get_engine().dispose()

    asyncio.run(_seed())

    raw_lap = {
        "session_id": str(session_row.id),
        "driver_id": str(driver.id),
        "lap_number": 12,
        "lap_time_seconds": 91.234,
        "compound": "MEDIUM",
        "tyre_age_laps": 12,
        "is_valid": True,
        "sector1_seconds": 28.1,
        "sector2_seconds": 35.0,
        "sector3_seconds": 28.134,
    }

    process_lap.delay(raw_lap).get()
    run_strategy_prediction.delay(raw_lap).get()

    async def _assert_persisted() -> None:
        async with db_session_factory() as db:
            lap_result = await db.execute(
                select(LapData).where(LapData.session_id == session_row.id)
            )
            assert lap_result.scalar_one_or_none() is not None

            prediction_result = await db.execute(
                select(StrategyPrediction).where(StrategyPrediction.session_id == session_row.id)
            )
            prediction = prediction_result.scalar_one()
            assert prediction.driver_id == driver.id
            assert prediction.model_version == "production"
            assert prediction.pit_probability == pytest.approx(0.8)
        await get_engine().dispose()

    asyncio.run(_assert_persisted())


def _trained_tire_pipeline(slope: float, seed: int) -> object:
    """Real fitted StandardScaler->XGBRegressor pipeline — SHAP's TreeExplainer
    needs an actual tree estimator, not a MagicMock (same technique as
    test_strategy_endpoint.py's _trained_pipeline)."""
    rng = np.random.default_rng(seed)
    n = 100
    tyre_age_idx = FEATURE_COLUMNS.index("tyre_age_laps")
    features = rng.random((n, len(FEATURE_COLUMNS)))
    features[:, tyre_age_idx] = rng.uniform(0, 40, n)
    target = slope * features[:, tyre_age_idx] + rng.normal(0, 0.05, n)
    pipeline = _build_pipeline()
    pipeline.fit(features, target)
    return pipeline


def _trained_pit_predictor() -> lgb.LGBMClassifier:
    rng = np.random.default_rng(7)
    n = 100
    features = rng.random((n, len(pit_predictor_module.FEATURE_COLUMNS)))
    target = rng.integers(0, 2, n)
    model = lgb.LGBMClassifier(n_estimators=10, verbosity=-1)
    model.fit(features, target)
    return model


@pytest.fixture
def _stub_real_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real fitted models for every registry entry — unlike _stub_models
    above (one shared MagicMock, fine for _run_inference's own plain-
    predict()/predict_proba() calls), the Checkpoint 4 recommendation
    pipeline also runs real SHAP TreeExplainer calls
    (tire_deg_recommendation_contributions/pit_predictor_current_
    contributions), which require genuine fitted estimators."""
    stub_registry: dict[str, object] = {
        "tire_deg_soft.pkl": _trained_tire_pipeline(slope=-0.5, seed=101),
        "tire_deg_medium.pkl": _trained_tire_pipeline(slope=0.1, seed=102),
        "tire_deg_hard.pkl": _trained_tire_pipeline(slope=0.05, seed=103),
        "tire_deg_inter.pkl": _trained_tire_pipeline(slope=0.2, seed=104),
        "tire_deg_wet.pkl": _trained_tire_pipeline(slope=0.2, seed=105),
        "pit_predictor.pkl": _trained_pit_predictor(),
    }
    sc_model = MagicMock()
    sc_model.probability_within.return_value = 0.05
    stub_registry["safety_car_model.pkl"] = sc_model
    monkeypatch.setattr(prediction_worker, "_load_models", lambda: stub_registry)


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_real_models")
def test_live_prediction_pipeline_populates_recommendation_fields(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Checkpoint 4's actual deliverable: the per-lap live/replay pipeline
    (not just the on-demand /pit-window REST endpoint) persists a real
    recommendation + explanation, and the optimal_pit_lap/tire_life_remaining
    bugfixes hold on a real round trip through the Celery dispatch."""
    circuit = Circuit(id=uuid.uuid4(), name="Test Circuit", country="Testland", track_length_km=5.0)
    race = Race(
        id=uuid.uuid4(),
        season=2025,
        round_number=2,
        circuit_id=circuit.id,
        race_date=date(2025, 3, 8),
        status="in_progress",
    )
    session_row = SessionModel(
        id=uuid.uuid4(), race_id=race.id, session_type="R", session_date=date(2025, 3, 8)
    )
    driver = Driver(id=uuid.uuid4(), code="VER", full_name="Max Verstappen", nationality="NED")
    # A second, further-along driver establishes a real race distance
    # (total_laps is estimated as MAX(lap_number) across the session — see
    # strategy_service.py's module docstring) so compute_pit_recommendation
    # has a non-empty [current_lap+1, total_laps] candidate range.
    other_driver = Driver(
        id=uuid.uuid4(), code="HAM", full_name="Lewis Hamilton", nationality="GBR"
    )
    far_lap = LapData(
        id=uuid.uuid4(),
        session_id=session_row.id,
        driver_id=other_driver.id,
        lap_number=40,
        compound="MEDIUM",
        tyre_age_laps=15,
        lap_time_seconds=91.0,
        position=1,
    )

    async def _seed() -> None:
        async with db_session_factory() as db:
            db.add_all([circuit, race, session_row, driver, other_driver, far_lap])
            await db.commit()
        await get_engine().dispose()

    asyncio.run(_seed())

    raw_lap = {
        "session_id": str(session_row.id),
        "driver_id": str(driver.id),
        "lap_number": 12,
        "lap_time_seconds": 91.234,
        "compound": "MEDIUM",
        "tyre_age_laps": 12,
        "is_valid": True,
        "sector1_seconds": 28.1,
        "sector2_seconds": 35.0,
        "sector3_seconds": 28.134,
        "position": 2,
    }

    process_lap.delay(raw_lap).get()
    run_strategy_prediction.delay(raw_lap).get()

    async def _assert_persisted() -> None:
        async with db_session_factory() as db:
            prediction_result = await db.execute(
                select(StrategyPrediction).where(
                    StrategyPrediction.session_id == session_row.id,
                    StrategyPrediction.driver_id == driver.id,
                )
            )
            prediction = prediction_result.scalar_one()

            # The recommendation-engine fields (Checkpoint 2/3's own output,
            # persisted here for the first time).
            assert prediction.recommended_pit_lap is not None
            assert 13 <= prediction.recommended_pit_lap <= 40
            assert prediction.window_start is not None
            assert prediction.window_end is not None
            assert (
                prediction.window_start <= prediction.recommended_pit_lap <= prediction.window_end
            )
            assert prediction.recommended_compound in {"SOFT", "MEDIUM", "HARD"}
            assert 0.0 <= prediction.confidence_score <= 1.0

            explanation = prediction.explanation
            assert explanation is not None
            assert isinstance(explanation["narrative"], str)
            assert explanation["narrative"]
            assert len(explanation["facts"]) > 0
            assert len(explanation["tire_deg_shap"]) > 0
            # pit_predictor.pkl and the current compound's tire_deg pipeline
            # are both loaded, so this runs regardless of whether a real
            # track-position neighbour resolves (the far_lap driver is
            # excluded by _resolve_position_context's own lap_number <= 12
            # bound, so this driver's own gap features fall back to
            # MAX_GAP_SECONDS — a real, structurally-valid SHAP input all
            # the same, not an empty one).
            assert len(explanation["pit_predictor_shap"]) > 0

            # The two bugfixes: tire_life_remaining is a genuine laps-count
            # (0..MAX_LOOKAHEAD_LAPS, never the old raw small-delta value),
            # and optimal_pit_lap is internally consistent with it — not
            # collapsed to lap_number + 1 by construction.
            assert 0.0 <= prediction.tire_life_remaining <= 40.0
            assert prediction.optimal_pit_lap == 12 + max(int(prediction.tire_life_remaining), 1)
        await get_engine().dispose()

    asyncio.run(_assert_persisted())
