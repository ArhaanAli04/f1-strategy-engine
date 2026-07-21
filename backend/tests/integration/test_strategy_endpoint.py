"""Integration tests for the strategy API against a real DB + Redis + (eager) Celery.

test_pit_window: GET /strategy/{session_id}/{driver_id}/pit-window calls
strategy_service.get_pit_window_with_explanation, which runs SHAP's
TreeExplainer against a real fitted sklearn Pipeline — a MagicMock model
object doesn't satisfy SHAP's API. Same fix as tests/conftest.py's
trained_tire_model unit-test fixture: monkeypatch strategy_service._load_models
to return real (synthetic-data-fit) StandardScaler->XGBRegressor pipelines,
shaped exactly like the production tire_deg_*.pkl models, instead of loading
from S3.

test_simulate: per CLAUDE.md's Day 16 spec note, no real Celery worker is
started — the broker/worker serialization path is already covered by
test_race_simulation_serialization.py. Scoped instead to what's meaningfully
testable without one: flip task_always_eager (+ task_store_eager_result, so
the eager run's result is actually written to the real Redis backend, not
just held in the in-process EagerResult) so POST /simulate's .delay() call
runs run_race_simulation synchronously in-process, then poll
GET /simulate/{task_id} once and confirm the real dispatch -> result-backend
-> poll -> schema-parse path round-trips correctly.
"""

import uuid
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.redis import RedisContainer

from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData
from backend.services import strategy_service
from backend.services.ml.tire_deg_model import FEATURE_COLUMNS, _build_pipeline
from backend.tests.integration.conftest import seed_via_test_client
from backend.workers import prediction_worker
from backend.workers.celery_app import app as celery_app


def _trained_pipeline() -> object:
    """A synthetic Pipeline fit on random data, correct FEATURE_COLUMNS shape —
    same technique as tests/conftest.py's trained_tire_model unit fixture,
    needed here because explainability.explain_prediction runs a real SHAP
    TreeExplainer that requires an actual fitted model, not a MagicMock.
    """
    rng = np.random.default_rng(42)
    n_samples = 50
    features = rng.random((n_samples, len(FEATURE_COLUMNS)))
    target = rng.normal(0.0, 1.0, n_samples)
    pipeline = _build_pipeline()
    pipeline.fit(features, target)
    return pipeline


def _seed_session_with_lap(
    test_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession], compound: str
) -> tuple[uuid.UUID, uuid.UUID]:
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
    lap = LapData(
        id=uuid.uuid4(),
        session_id=session_row.id,
        driver_id=driver.id,
        lap_number=12,
        compound=compound,
        tyre_age_laps=12,
        lap_time_seconds=91.2,
    )
    # get_optimal_pit_window derives total_laps as MAX(lap_number) across the
    # whole session (races/sessions don't persist a race-distance column —
    # see strategy_service.py's module docstring). Without a lap somewhere in
    # the session beyond the driver's own latest lap, total_laps == 12 ==
    # lap_number, so the [lap_number+1, total_laps] candidate window is empty
    # and the endpoint would (correctly) return []. A second car, further
    # into the race, establishes a realistic race distance.
    other_driver = Driver(
        id=uuid.uuid4(), code="HAM", full_name="Lewis Hamilton", nationality="GBR"
    )
    far_lap = LapData(
        id=uuid.uuid4(),
        session_id=session_row.id,
        driver_id=other_driver.id,
        lap_number=50,
        compound=compound,
        tyre_age_laps=10,
        lap_time_seconds=91.0,
    )
    seed_via_test_client(
        test_client,
        db_session_factory,
        circuit,
        race,
        session_row,
        driver,
        other_driver,
        lap,
        far_lap,
    )
    return session_row.id, driver.id


@pytest.mark.integration
def test_pit_window_endpoint_returns_valid_schema(
    test_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_models = {
        "tire_deg_soft.pkl": _trained_pipeline(),
        "tire_deg_medium.pkl": _trained_pipeline(),
        "tire_deg_hard.pkl": _trained_pipeline(),
    }
    monkeypatch.setattr(strategy_service, "_load_models", lambda: stub_models)

    session_id, driver_id = _seed_session_with_lap(test_client, db_session_factory, "MEDIUM")

    response = test_client.get(f"/api/v1/strategy/{session_id}/{driver_id}/pit-window")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    for candidate in body:
        assert {"pit_lap", "window_start", "window_end", "projected_total_delta_seconds"} <= set(
            candidate
        )
    # Only the top (first) recommendation carries a SHAP explanation, capped
    # at explainability.DEFAULT_TOP_K (5) highest-magnitude contributions —
    # not one entry per FEATURE_COLUMNS (6) feature (see explain_prediction's
    # docstring: "Top-k SHAP feature contributions ... sorted by |contribution|
    # descending").
    assert body[0]["shap_explanation"] is not None
    assert 1 <= len(body[0]["shap_explanation"]) <= len(FEATURE_COLUMNS)
    for contribution in body[0]["shap_explanation"]:
        assert contribution["feature_name"] in FEATURE_COLUMNS
        assert contribution["direction"] in ("+", "-")


@pytest.fixture
def _eager_celery_with_stored_results(redis_container: RedisContainer) -> None:
    """Run Celery tasks synchronously in-process, but still write the result
    to the real Redis backend (task_store_eager_result) — the default eager
    behavior only keeps the result in the in-process EagerResult object,
    which GET /simulate/{task_id}'s independent AsyncResult(task_id, ...)
    lookup would never see.

    celery_app is a module-level singleton (backend/workers/celery_app.py:
    `app = Celery(..., broker=_redis_url, backend=_redis_url)`) built from
    REDIS_URL at first import of that module — same hazard as
    core/rate_limit.py's Limiter. Unlike the Limiter, lazily importing
    backend.main inside test_client doesn't save us here: other integration
    test files (test_live_prediction_pipeline.py, test_race_simulation_serialization.py)
    already import backend.workers.celery_app at their own module's top
    level, which Python resolves at collection time — before
    _point_settings_at_containers has redirected REDIS_URL — so by the time
    this test runs, the already-cached celery_app module object is bound to
    whatever REDIS_URL was set outside the container. Repointing
    conf.broker_url/result_backend alone isn't enough, though: app.backend is
    itself a cached property (celery/app/base.py — cached in
    self._backend_cache once first resolved from conf, thread-safe backends
    like Redis's don't re-check conf on every access). If any EARLIER
    integration test in this session already touched celery_app.backend
    (confirmed: test_live_prediction_pipeline.py's eager .delay().get() calls
    do, even without task_store_eager_result), that cached backend object is
    already bound to the wrong pre-redirect URL, and reassigning conf
    afterward silently does nothing. Clearing _backend_cache (and
    _local.backend, the non-thread-safe fallback path) forces the next
    access to rebuild from the now-correct conf.

    A second, separate caching layer bites here too: celery/app/task.py's
    Task.bind() copies task_store_eager_result into the Task CLASS's
    store_eager_result attribute only `if getattr(cls, attr_name, None) is
    None` — i.e. once any task class has been bound once (with the config's
    default, False, since nothing had set task_store_eager_result=True yet),
    every later conf change is silently ignored for that task class.
    test_race_simulation_serialization.py's run_race_simulation.run(...) call
    triggers exactly that binding earlier in the session. Confirmed via a
    debug run: conf/broker/backend were all already correct, yet the stored
    result still never appeared — setting store_eager_result directly on the
    task object bypasses the bind() gate entirely.
    """
    redis_url = (
        f"redis://{redis_container.get_container_host_ip()}:"
        f"{redis_container.get_exposed_port(6379)}"
    )
    celery_app.conf.broker_url = redis_url
    celery_app.conf.result_backend = redis_url
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    celery_app.conf.task_store_eager_result = True
    celery_app._backend_cache = None
    celery_app._local.__dict__.pop("backend", None)
    prediction_worker.run_race_simulation.store_eager_result = True


@pytest.fixture
def _stub_simulation_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand-in ML models shaped for race_simulator's batch calls — same
    fixture as test_race_simulation_serialization.py's _stub_models.
    """
    stub_model = MagicMock()
    stub_model.predict.side_effect = lambda features: np.full(len(features), 0.05)
    stub_model.predict_proba.side_effect = lambda features: np.column_stack(
        [np.full(len(features), 0.8), np.full(len(features), 0.2)]
    )
    stub_model.probability_within.return_value = 0.0

    stub_registry = dict.fromkeys(prediction_worker._MODEL_FILES, stub_model)
    monkeypatch.setattr(prediction_worker, "_load_models", lambda: stub_registry)


@pytest.mark.integration
@pytest.mark.usefixtures("_eager_celery_with_stored_results", "_stub_simulation_models")
def test_simulate_returns_task_id(
    test_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
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
    seed_via_test_client(test_client, db_session_factory, circuit, race, session_row, driver)

    # simulate_strategy's .delay() call runs (in eager mode) on a brand-new
    # event loop inside _SIMULATE_ENQUEUE_EXECUTOR's thread, via
    # prediction_worker._run_simulation's own asyncio.run() — a different
    # loop than test_client's persistent portal loop. get_engine() is a
    # process-wide singleton pool; the seeding call above (and TestClient's
    # own lifespan startup health check) already left connections in it bound
    # to the portal loop. pool_pre_ping would try to ping one of those from
    # the executor thread's loop and hit an asyncpg cross-loop RuntimeError.
    # Disposing here (still on the portal loop, so it can close them
    # gracefully) empties the pool first, forcing the executor thread to open
    # its own fresh, correctly-loop-bound connection.
    test_client.portal.call(get_engine().dispose)  # type: ignore[union-attr]

    payload = {
        "driver_id": str(driver.id),
        "current_lap": 1,
        "current_compound": "MEDIUM",
        "current_tyre_age": 2,
        "remaining_laps": 3,
        "pit_laps": [],
        "compounds": [],
    }
    response = test_client.post(f"/api/v1/strategy/{session_row.id}/simulate", json=payload)

    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert task_id

    poll_response = test_client.get(f"/api/v1/strategy/simulate/{task_id}")

    assert poll_response.status_code == 200
    poll_body = poll_response.json()
    assert poll_body["status"] == "SUCCESS"
    assert poll_body["result"] is not None
    assert poll_body["result"]["driver_id"] == str(driver.id)
