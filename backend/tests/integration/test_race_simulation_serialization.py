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

Also covers position_probabilities/mean_position (added for the What-If
Simulator multi-scenario rebuild, Checkpoint 2 — see
docs/core-feature-rebuild-whatif-simulator.md): position_probabilities is a
list[PositionProbability] (each a plain {position, probability} object), not
a dict, specifically to avoid depending on Pydantic coercing a JSON object's
string keys back to int — this test confirms that shape round-trips too.

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
from backend.schemas.simulate_schema import (
    PositionProbability,
    ScenarioPlan,
    SimulateStrategyRequest,
    SimulateStrategyResponse,
)
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
    raw_strategy = raw_result["strategies"][0]
    raw_confidence_interval = raw_strategy["confidence_interval"]
    assert isinstance(raw_confidence_interval, tuple)

    # Single driver, no lap_data seeded (see _build_race_state's
    # requesting_driver_found=False branch) — race_simulator.simulate_race
    # trivially ranks a lone driver P1 in every simulation, so this is a
    # degenerate but real position_probabilities value, not a mock.
    assert raw_strategy["mean_position"] == pytest.approx(1.0)
    assert raw_strategy["position_probabilities"] == [{"position": 1, "probability": 1.0}]
    # starting_position falls back to len(latest_laps) + 1 = 1 for a
    # requester with no persisted lap data (see _build_race_state's
    # requesting_driver_found=False branch) — same fallback that produces
    # the degenerate P1 distribution above.
    assert raw_result["starting_position"] == 1

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
    round_tripped_strategy = round_tripped_result["strategies"][0]
    round_tripped_confidence_interval = round_tripped_strategy["confidence_interval"]
    assert isinstance(round_tripped_confidence_interval, list)
    # position_probabilities was always a list (not a dict), so it has no
    # equivalent "changes JSON type" concern — this just confirms the round
    # trip preserves it exactly, dict-key-string-coercion concern included.
    assert round_tripped_strategy["position_probabilities"] == [{"position": 1, "probability": 1.0}]

    # Same call apis/v1/strategy.py's get_simulation_result makes on
    # AsyncResult(task_id).result.
    parsed = SimulateStrategyResponse.model_validate(round_tripped_result)
    parsed_strategy = parsed.strategies[0]
    parsed_confidence_interval = parsed_strategy.confidence_interval

    assert isinstance(parsed_confidence_interval, tuple)
    assert parsed_confidence_interval == pytest.approx(raw_confidence_interval)
    assert parsed_strategy.mean_position == pytest.approx(1.0)
    assert parsed.starting_position == 1
    assert parsed_strategy.position_probabilities == [
        PositionProbability(position=1, probability=1.0)
    ]


# --- Multi-scenario compare (Checkpoint 3, see
# docs/core-feature-rebuild-whatif-simulator.md): SimulateStrategyRequest.
# scenarios runs N race_simulator.simulate_race calls off ONE
# _build_race_state, sharing one random seed across all N calls ("common
# random numbers" — see prediction_worker._run_one_scenario's docstring). ---


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_models")
def test_multi_scenario_shares_rng_seed_and_preserves_order_and_labels(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two scenarios with an IDENTICAL forced pit plan must produce
    bit-identical Monte Carlo outcomes when they share one seed — this is
    true regardless of what the stub models return (it's a property of the
    RNG draw sequence, not of model behaviour), so it's a robust proof that
    _run_simulation really does share ONE seed across every scenario in a
    request rather than drawing a fresh one per scenario (which would defeat
    the whole point: isolating the comparison to each scenario's own pit-lap
    decision). A third, genuinely different scenario confirms the request
    isn't just collapsing to one hardcoded plan regardless of input.
    """
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
        await get_engine().dispose()

    asyncio.run(_seed())

    request = SimulateStrategyRequest(
        driver_id=driver.id,
        current_lap=1,
        current_compound="MEDIUM",
        current_tyre_age=2,
        remaining_laps=3,
        scenarios=[
            ScenarioPlan(pit_laps=[2], compounds=["HARD"], label="Copy A"),
            ScenarioPlan(pit_laps=[2], compounds=["HARD"], label="Copy B"),
            ScenarioPlan(pit_laps=[3], compounds=["HARD"], label="Different lap"),
        ],
    )
    task_payload = {"session_id": str(session_row.id), **request.model_dump(mode="json")}

    raw_result = run_race_simulation.run(task_payload)
    strategies = raw_result["strategies"]

    assert len(strategies) == 3
    assert [s["label"] for s in strategies] == ["Copy A", "Copy B", "Different lap"]
    assert [s["pit_laps"] for s in strategies] == [[2], [2], [3]]
    assert [s["compounds"] for s in strategies] == [["HARD"], ["HARD"], ["HARD"]]

    # Shared seed: identical forced pit plans (scenario 0 and 1) must be
    # bit-identical, not just approximately close.
    assert strategies[0]["mean_position"] == strategies[1]["mean_position"]
    assert strategies[0]["predicted_finish_time"] == strategies[1]["predicted_finish_time"]
    assert strategies[0]["position_probabilities"] == strategies[1]["position_probabilities"]
    assert strategies[0]["confidence_interval"] == strategies[1]["confidence_interval"]

    # Round-trip through the real Celery result backend once more, this time
    # for the multi-scenario shape specifically — confirms the list-of-3
    # survives JSON encode/decode and re-parses correctly in request order.
    parsed = SimulateStrategyResponse.model_validate(raw_result)
    assert [s.label for s in parsed.strategies] == ["Copy A", "Copy B", "Different lap"]


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_models")
def test_single_plan_request_still_produces_exactly_one_strategy(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Regression guard: a request with no scenarios (the pre-Checkpoint-3
    shape) must still return exactly one strategy with label=None — the
    multi-scenario branch must never be taken when scenarios is absent."""
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
        await get_engine().dispose()

    asyncio.run(_seed())

    request = SimulateStrategyRequest(
        driver_id=driver.id,
        current_lap=1,
        current_compound="MEDIUM",
        current_tyre_age=2,
        remaining_laps=3,
        pit_laps=[2],
        compounds=["HARD"],
    )
    task_payload = {"session_id": str(session_row.id), **request.model_dump(mode="json")}

    raw_result = run_race_simulation.run(task_payload)

    assert len(raw_result["strategies"]) == 1
    assert raw_result["strategies"][0]["label"] is None
    assert raw_result["strategies"][0]["pit_laps"] == [2]
    assert raw_result["starting_position"] == 1
