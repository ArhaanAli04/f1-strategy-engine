"""Pre-Day-14C verification: does confidence_interval survive Celery's result backend?

run_race_simulation returns a dict whose confidence_interval field is a Python
tuple (see prediction_worker._run_simulation). Celery's task_serializer/
result_serializer are both "json" (workers/celery_app.py), and JSON has no
tuple type — a tuple becomes a JSON array on the wire and comes back out of
Redis as a list. apis/v1/strategy.py's get_simulation_result then calls
SimulateStrategyResponse.model_validate(result.result) on whatever the
backend handed back. This was flagged in CLAUDE.md's Deferred Wiring as
untested against a real ML pipeline + real result backend — this test closes
that gap.

Deliberately does NOT use Celery's eager mode (task_always_eager): eager
execution returns the raw Python object directly, without ever going through
JSON encode/decode, so it would not exercise the thing in question. Instead
this calls the real task body to get a real return value, then round-trips
that value through a real celery.backends.redis.RedisBackend (pointed at the
integration Redis container) using the exact serializer config
workers/celery_app.py uses in production.
"""

import asyncio
import uuid
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pytest
from celery import Celery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.redis import RedisContainer

from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.schemas.simulate_schema import SimulateStrategyRequest, SimulateStrategyResponse
from backend.workers import prediction_worker
from backend.workers.prediction_worker import run_race_simulation


@pytest.fixture
def _stub_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand-in ML models, shaped for race_simulator's BATCH calls.

    Unlike test_live_prediction_pipeline.py's _stub_models (one row per
    call), race_simulator.py calls .predict()/.predict_proba() once per lap
    across the full (n_simulations x n_drivers) state matrix — the stub must
    return an array matching whatever batch size it's called with, not a
    fixed-length constant.
    """
    stub_model = MagicMock()
    stub_model.predict.side_effect = lambda features: np.full(len(features), 0.05)
    stub_model.predict_proba.side_effect = lambda features: np.column_stack(
        [np.full(len(features), 0.8), np.full(len(features), 0.2)]
    )
    stub_model.probability_within.return_value = 0.0  # no safety car, deterministic

    stub_registry = dict.fromkeys(prediction_worker._MODEL_FILES, stub_model)
    monkeypatch.setattr(prediction_worker, "_load_models", lambda: stub_registry)


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_models")
def test_confidence_interval_round_trips_through_celery_result_backend(
    db_session_factory: async_sessionmaker[AsyncSession],
    redis_container: RedisContainer,
) -> None:
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
        # See db_session_factory's docstring: dispose before the next
        # separately-asyncio.run()'d unit of work.
        await get_engine().dispose()

    asyncio.run(_seed())

    request = SimulateStrategyRequest(
        driver_id=driver.id,
        current_lap=1,
        current_compound="MEDIUM",
        current_tyre_age=2,
        remaining_laps=3,
        pit_laps=[],
        compounds=[],
    )
    task_payload = {"session_id": str(session_row.id), **request.model_dump(mode="json")}

    # The real task body — real _run_simulation -> real race_simulator.simulate_race
    # -> a real confidence_interval tuple. .run() calls the task function
    # directly (no broker/backend involved yet), matching how
    # test_live_prediction_pipeline.py invokes tasks outside of .delay().
    raw_result = run_race_simulation.run(task_payload)
    raw_confidence_interval = raw_result["strategies"][0]["confidence_interval"]
    assert isinstance(raw_confidence_interval, tuple)

    # Isolated Celery app, pointed at the integration Redis container, with
    # the identical serializer config workers/celery_app.py uses in
    # production — NOT the shared celery_app singleton, to avoid depending on
    # whatever broker/backend URL it was constructed with at import time.
    redis_url = (
        f"redis://{redis_container.get_container_host_ip()}:"
        f"{redis_container.get_exposed_port(6379)}/1"
    )
    backend_app = Celery("test_result_backend", broker=redis_url, backend=redis_url)
    backend_app.conf.update(
        task_serializer="json", result_serializer="json", accept_content=["json"]
    )

    task_id = str(uuid.uuid4())
    backend_app.backend.store_result(task_id, raw_result, "SUCCESS")
    task_meta = backend_app.backend.get_task_meta(task_id)
    round_tripped_result = task_meta["result"]

    # JSON has no tuple type — confirms *why* this needed verifying at all.
    round_tripped_confidence_interval = round_tripped_result["strategies"][0]["confidence_interval"]
    assert isinstance(round_tripped_confidence_interval, list)

    # Same call apis/v1/strategy.py's get_simulation_result makes on
    # AsyncResult(task_id).result.
    parsed = SimulateStrategyResponse.model_validate(round_tripped_result)
    parsed_confidence_interval = parsed.strategies[0].confidence_interval

    assert isinstance(parsed_confidence_interval, tuple)
    assert parsed_confidence_interval == pytest.approx(raw_confidence_interval)
