"""Day 6 verification: a mock lap-completion event flows through Celery into Postgres.

Mirrors what ingest_live_session.py does on a real lap-completion event —
dispatches process_lap (telemetry_queue) and run_strategy_prediction
(prediction_queue) with the same raw lap dict. ML models are mocked; per the
Day 6 spec, they don't exist until Day 7 — placeholder scores are expected.
"""

import asyncio
import uuid
from datetime import date
from unittest.mock import MagicMock

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
            assert prediction.model_version == "latest"
            assert prediction.pit_probability == pytest.approx(0.8)
        await get_engine().dispose()

    asyncio.run(_assert_persisted())
