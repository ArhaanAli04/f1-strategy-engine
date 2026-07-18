"""Shared unit-test fixtures: mocks only — no real DB or Redis touched here.

Integration-test fixtures (real Postgres + Redis via testcontainers) live in
backend/tests/integration/conftest.py instead, since they're heavier and
scoped only to that test tier.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import fakeredis as fakeredis_lib
import numpy as np
import pytest
import pytest_asyncio
from sklearn.pipeline import Pipeline
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.ml.tire_deg_model import FEATURE_COLUMNS, _build_pipeline


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """A spec'd AsyncSession mock — auto-detects async vs sync methods from the spec."""
    return AsyncMock(spec=AsyncSession)


@pytest_asyncio.fixture
async def fakeredis() -> AsyncGenerator[fakeredis_lib.FakeAsyncRedis, None]:
    """An in-memory fakeredis client standing in for a real Redis connection."""
    client = fakeredis_lib.FakeAsyncRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()  # type: ignore[attr-defined]


@pytest.fixture
def sample_lap_data() -> list[dict[str, Any]]:
    """A valid 5-lap stint's worth of LapData dicts, same session/driver, aging tyre."""
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    return [
        {
            "id": uuid.uuid4(),
            "session_id": session_id,
            "driver_id": driver_id,
            "lap_number": lap_number,
            "lap_time_seconds": 90.0 + 0.2 * (lap_number - 1),
            "compound": "MEDIUM",
            "tyre_age_laps": lap_number - 1,
            "is_valid": True,
            "position": 3,
            "track_status": "1",
            "sector1_seconds": 28.0,
            "sector2_seconds": 34.5,
            "sector3_seconds": 27.5 + 0.2 * (lap_number - 1),
            "track_temp": 35.0,
            "air_temp": 25.0,
        }
        for lap_number in range(1, 6)
    ]


@pytest.fixture
def sample_strategy_prediction() -> dict[str, Any]:
    """A valid StrategyPrediction dict — all required columns from the model."""
    return {
        "id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "driver_id": uuid.uuid4(),
        "predicted_at": datetime.now(UTC),
        "optimal_pit_lap": 22,
        "pit_probability": 0.8,
        "undercut_score": 0.63,
        "overcut_score": 0.37,
        "tire_life_remaining": 8.0,
        "confidence_score": 0.75,
        "model_version": "production",
    }


@pytest.fixture
def trained_tire_model() -> Pipeline:
    """A synthetic tire_deg Pipeline fit on random data — NOT loaded from models/.

    models/ is gitignored and absent in CI, so real .pkl files can't be a unit-test
    fixture. This fits the real StandardScaler->XGBRegressor pipeline shape on random
    data with the correct 6-column FEATURE_COLUMNS shape, so tests exercise real
    pipeline mechanics (shape validation, .predict() contract) without asserting
    anything about real tyre-degradation behavior — that's covered by integration
    tests against the actual promoted models.
    """
    rng = np.random.default_rng(42)
    n_samples = 50
    features = rng.random((n_samples, len(FEATURE_COLUMNS)))
    target = rng.normal(0.0, 1.0, n_samples)
    pipeline = _build_pipeline()
    pipeline.fit(features, target)
    return pipeline
