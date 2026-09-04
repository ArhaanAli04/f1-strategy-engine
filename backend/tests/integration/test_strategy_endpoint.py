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

import asyncio
import uuid
from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.redis import RedisContainer

from backend.core.database import get_engine
from backend.core.exceptions import ValidationError
from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData
from backend.services import strategy_service
from backend.services.ml import race_simulator
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
    # build_pit_recommendation derives total_laps as MAX(lap_number) across the
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
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_models = {
        "tire_deg_soft.pkl": _trained_pipeline(),
        "tire_deg_medium.pkl": _trained_pipeline(),
        "tire_deg_hard.pkl": _trained_pipeline(),
    }
    monkeypatch.setattr(strategy_service, "_load_models", lambda: stub_models)

    session_id, driver_id = _seed_session_with_lap(
        authenticated_client, db_session_factory, "MEDIUM"
    )

    response = authenticated_client.get(f"/api/v1/strategy/{session_id}/{driver_id}/pit-window")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    for candidate in body:
        assert {"pit_lap", "window_start", "window_end", "projected_total_delta_seconds"} <= set(
            candidate
        )
    # Only the top (first) recommendation carries an explanation. Its
    # tire_deg_shap is capped at explainability.DEFAULT_TOP_K (5)
    # highest-magnitude contributions — not one entry per FEATURE_COLUMNS (6)
    # feature (see explain_prediction's docstring: "Top-k SHAP feature
    # contributions ... sorted by |contribution| descending"). pit_predictor_shap
    # is empty here — this test's stub_models has no pit_predictor.pkl loaded
    # (_pit_predictor_current_contributions degrades to [] gracefully rather
    # than raising), and neither seeded LapData row has a position set, so
    # get_undercut_score/get_overcut_score are never even called (no resolvable
    # track-position neighbour) — still a valid 200, just a narrower explanation.
    explanation = body[0]["explanation"]
    assert explanation is not None
    assert 1 <= len(explanation["tire_deg_shap"]) <= len(FEATURE_COLUMNS)
    for contribution in explanation["tire_deg_shap"]:
        assert contribution["feature_name"] in FEATURE_COLUMNS
        assert contribution["direction"] in ("+", "-")
    assert explanation["pit_predictor_shap"] == []
    assert isinstance(explanation["narrative"], str)
    assert explanation["narrative"]
    assert len(explanation["facts"]) > 0
    for candidate in body[1:]:
        assert candidate["explanation"] is None


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
def _eager_celery_captures_failures(redis_container: RedisContainer) -> None:
    """Same as _eager_celery_with_stored_results, but task_eager_propagates=False.

    With propagates=True (the other fixture), an exception raised inside the
    task re-raises synchronously at the .delay()/.run() call site — exactly
    what test_run_race_simulation_rejects_excessive_current_lap_when_route_bypassed
    wants (pytest.raises around .run()). GET /simulate/{task_id}'s FAILURE/error
    surfacing (this file's two _eager_celery_captures_failures tests) needs the
    opposite: the exception caught by Celery's own eager machinery and stored as
    a real FAILURE result in the backend, so there's a task_id to actually poll —
    matching real (non-eager) worker behavior, where a task exception always
    becomes a stored FAILURE, never a synchronous raise at the enqueue site.
    """
    redis_url = (
        f"redis://{redis_container.get_container_host_ip()}:"
        f"{redis_container.get_exposed_port(6379)}"
    )
    celery_app.conf.broker_url = redis_url
    celery_app.conf.result_backend = redis_url
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
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
    authenticated_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
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
    seed_via_test_client(
        authenticated_client, db_session_factory, circuit, race, session_row, driver
    )

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
    authenticated_client.portal.call(get_engine().dispose)  # type: ignore[union-attr]

    payload = {
        "driver_id": str(driver.id),
        "current_lap": 1,
        "current_compound": "MEDIUM",
        "current_tyre_age": 2,
        "remaining_laps": 3,
        "pit_laps": [],
        "compounds": [],
    }
    response = authenticated_client.post(
        f"/api/v1/strategy/{session_row.id}/simulate", json=payload
    )

    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert task_id

    poll_response = authenticated_client.get(f"/api/v1/strategy/simulate/{task_id}")

    assert poll_response.status_code == 200
    poll_body = poll_response.json()
    assert poll_body["status"] == "SUCCESS"
    assert poll_body["result"] is not None
    assert poll_body["result"]["driver_id"] == str(driver.id)


# --- POST /simulate: current_lap-vs-session-progress validation ---
# See docs/simulator-issues-wet-model-and-position-context.md's Checkpoint-6
# follow-up finding: current_lap=68 was silently accepted (and simulated!)
# for a session whose real race was 44 laps. strategy_service
# .validate_current_lap closes this; these tests cover the route-level call
# (Checkpoint 3) — the worker-level defense-in-depth call (Checkpoint 4,
# a caller that bypasses this route entirely) is covered separately.


@pytest.mark.integration
def test_simulate_rejects_current_lap_beyond_session_progress(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact bug: current_lap far beyond the session's real progress.

    _seed_session_with_lap's own driver reaches lap_number=12, and a second
    car (far_lap) reaches lap_number=50 — this session's real progress
    ceiling is therefore 50 + 1 = 51. Also asserts the route rejects BEFORE
    ever calling run_race_simulation.delay() — a bad current_lap must not
    cost a Celery round trip at all.
    """
    session_id, driver_id = _seed_session_with_lap(
        authenticated_client, db_session_factory, "MEDIUM"
    )
    never_enqueue = MagicMock(side_effect=AssertionError("must not enqueue a Celery task"))
    monkeypatch.setattr(prediction_worker.run_race_simulation, "delay", never_enqueue)

    payload = {
        "driver_id": str(driver_id),
        "current_lap": 68,
        "current_compound": "HARD",
        "current_tyre_age": 20,
        "remaining_laps": 5,
        "pit_laps": [],
        "compounds": [],
    }
    response = authenticated_client.post(f"/api/v1/strategy/{session_id}/simulate", json=payload)

    assert response.status_code == 422
    never_enqueue.assert_not_called()


@pytest.mark.integration
def test_simulate_rejects_unknown_session(
    authenticated_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    never_enqueue = MagicMock(side_effect=AssertionError("must not enqueue a Celery task"))
    monkeypatch.setattr(prediction_worker.run_race_simulation, "delay", never_enqueue)

    payload = {
        "driver_id": str(uuid.uuid4()),
        "current_lap": 1,
        "current_compound": "MEDIUM",
        "current_tyre_age": 2,
        "remaining_laps": 3,
        "pit_laps": [],
        "compounds": [],
    }
    response = authenticated_client.post(f"/api/v1/strategy/{uuid.uuid4()}/simulate", json=payload)

    assert response.status_code == 404
    never_enqueue.assert_not_called()


@pytest.mark.integration
@pytest.mark.usefixtures("_stub_simulation_models")
def test_run_race_simulation_rejects_excessive_current_lap_when_route_bypassed(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Defense in depth (Checkpoint 4): a caller that dispatches
    run_race_simulation directly — bypassing POST /simulate entirely, e.g. a
    future replay/backfill script — must not be able to skip the
    current_lap validation just by not going through the route. Calls the
    task body directly via .run() (no broker/backend involved), same
    pattern as test_race_simulation_serialization.py; Checkpoint 3's own
    tests already cover the route-level rejection, this covers the worker
    catching what the route never got a chance to.
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
    lap = LapData(
        id=uuid.uuid4(),
        session_id=session_row.id,
        driver_id=driver.id,
        lap_number=10,
        compound="MEDIUM",
        tyre_age_laps=10,
        lap_time_seconds=91.0,
    )

    async def _seed() -> None:
        async with db_session_factory() as db:
            db.add_all([circuit, race, session_row, driver, lap])
            await db.commit()
        # See db_session_factory's docstring: dispose before the next
        # separately-asyncio.run()'d unit of work.
        await get_engine().dispose()

    asyncio.run(_seed())

    # This session's real progress ceiling is 10 + 1 = 11 — current_lap=68
    # is the same class of gap the route-level tests cover, just reached by
    # calling the task directly instead of through the route.
    task_payload = {
        "session_id": str(session_row.id),
        "driver_id": str(driver.id),
        "current_lap": 68,
        "current_compound": "HARD",
        "current_tyre_age": 20,
        "remaining_laps": 5,
        "pit_laps": [],
        "compounds": [],
    }

    with pytest.raises(ValidationError):
        prediction_worker.run_race_simulation.run(task_payload)


# --- GET /simulate/{task_id}: FAILURE surfaces a user-facing error message ---
# See docs/day-deferred-fixes-session2-handoff.md item 12: SimulateTaskStatusResponse
# previously carried no failure reason at all, so a task FAILURE told the
# frontend nothing beyond the bare status string.


@pytest.mark.integration
@pytest.mark.usefixtures("_eager_celery_captures_failures", "_stub_simulation_models")
def test_simulation_failure_surfaces_f1_strategy_error_message(
    test_client: TestClient, db_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A known F1StrategyError's own .message is safe to pass through verbatim.

    Dispatches run_race_simulation.delay() directly (bypassing POST /simulate,
    same technique as the route-bypass test above) with current_lap=68 against
    a session whose real progress ceiling is 11 — the worker-level
    validate_current_lap call raises ValidationError, which real (non-eager)
    Celery would store as a FAILURE result; this fixture's eager-but-not-
    propagating config reproduces that same stored-FAILURE shape.
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
    lap = LapData(
        id=uuid.uuid4(),
        session_id=session_row.id,
        driver_id=driver.id,
        lap_number=10,
        compound="MEDIUM",
        tyre_age_laps=10,
        lap_time_seconds=91.0,
    )
    # seed_via_test_client + portal-loop dispose, not a bespoke asyncio.run()
    # — see test_simulate_returns_task_id's identical comment: test_client's
    # own lifespan startup already bound a pooled connection to its portal
    # loop, and a separate asyncio.run() from the test body would collide
    # with it (confirmed live while writing this test).
    seed_via_test_client(test_client, db_session_factory, circuit, race, session_row, driver, lap)
    test_client.portal.call(get_engine().dispose)  # type: ignore[union-attr]

    task_payload = {
        "session_id": str(session_row.id),
        "driver_id": str(driver.id),
        "current_lap": 68,
        "current_compound": "HARD",
        "current_tyre_age": 20,
        "remaining_laps": 5,
        "pit_laps": [],
        "compounds": [],
    }
    task = prediction_worker.run_race_simulation.delay(task_payload)

    response = test_client.get(f"/api/v1/strategy/simulate/{task.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILURE"
    assert body["result"] is None
    assert body["error"] is not None
    assert "68" in body["error"]  # ValidationError.message names the offending lap


@pytest.mark.integration
@pytest.mark.usefixtures("_eager_celery_captures_failures", "_stub_simulation_models")
def test_simulation_failure_hides_unexpected_exception_detail(
    test_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-F1StrategyError exception must never leak its own text — this route
    is unauthenticated (see apis/v1/strategy.py's module docstring), same
    reasoning as core/exceptions.py's unhandled_error_handler for every other
    route. Forces race_simulator.simulate_race to raise past a valid
    current_lap, so the failure is a genuine internal error, not a rejected
    request.
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
    # See the sibling test above for why this is seed_via_test_client + a
    # portal-loop dispose, not a bespoke asyncio.run().
    seed_via_test_client(test_client, db_session_factory, circuit, race, session_row, driver)
    test_client.portal.call(get_engine().dispose)  # type: ignore[union-attr]

    internal_exception_text = "connection to internal-db-host:5432 refused"
    # Patched via the direct `race_simulator` import (not
    # `prediction_worker.race_simulator`, an implicit re-export mypy --strict
    # rejects) — same module object either way, since prediction_worker.py's
    # own `from backend.services.ml import ... race_simulator ...` binds the
    # identical singleton module this patches.
    monkeypatch.setattr(
        race_simulator,
        "simulate_race",
        MagicMock(side_effect=RuntimeError(internal_exception_text)),
    )

    task_payload = {
        "session_id": str(session_row.id),
        "driver_id": str(driver.id),
        "current_lap": 1,
        "current_compound": "MEDIUM",
        "current_tyre_age": 2,
        "remaining_laps": 3,
        "pit_laps": [],
        "compounds": [],
    }
    task = prediction_worker.run_race_simulation.delay(task_payload)

    response = test_client.get(f"/api/v1/strategy/simulate/{task.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILURE"
    assert body["result"] is None
    assert body["error"] is not None
    assert internal_exception_text not in body["error"]


def _race_with_r_session(
    season: int,
    round_number: int,
    race_date: date,
    circuit: Circuit,
    driver: Driver,
    *,
    with_lap: bool,
) -> list[object]:
    """A Race + its R Session (+ optionally one LapData row), as a flat list of ORM rows."""
    race = Race(
        id=uuid.uuid4(),
        season=season,
        round_number=round_number,
        circuit_id=circuit.id,
        race_date=race_date,
        status="completed",
        event_name=f"Test GP {season} R{round_number}",
    )
    session_row = SessionModel(
        id=uuid.uuid4(), race_id=race.id, session_type="R", session_date=race_date
    )
    rows: list[object] = [race, session_row]
    if with_lap:
        rows.append(
            LapData(
                id=uuid.uuid4(),
                session_id=session_row.id,
                driver_id=driver.id,
                lap_number=1,
                compound="MEDIUM",
                tyre_age_laps=1,
                lap_time_seconds=90.0,
            )
        )
    return rows


@pytest.mark.integration
def test_last_ingested_session_returns_newest_with_lap_data(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    circuit = Circuit(id=uuid.uuid4(), name="Test Circuit", country="Testland", track_length_km=5.0)
    driver = Driver(id=uuid.uuid4(), code="VER", full_name="Max Verstappen", nationality="NED")
    older = _race_with_r_session(2025, 1, date(2025, 3, 1), circuit, driver, with_lap=True)
    target = _race_with_r_session(2026, 10, date(2026, 7, 19), circuit, driver, with_lap=True)
    # Newest race_date, but its R session has no lap_data — must be skipped.
    newest_no_data = _race_with_r_session(
        2026, 13, date(2026, 9, 6), circuit, driver, with_lap=False
    )

    seed_via_test_client(
        authenticated_client,
        db_session_factory,
        circuit,
        driver,
        *older,
        *target,
        *newest_no_data,
    )

    response = authenticated_client.get("/api/v1/strategy/last-ingested-session")

    assert response.status_code == 200
    body = response.json()
    assert body["season"] == 2026
    assert body["round_number"] == 10
    assert body["session_id"] == str(target[1].id)  # type: ignore[attr-defined]
    assert body["event_name"] == "Test GP 2026 R10"
    assert body["circuit_name"] == "Test Circuit"
    assert body["race_date"] == "2026-07-19"


@pytest.mark.integration
def test_last_ingested_session_404_when_no_ingested_races(
    authenticated_client: TestClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = authenticated_client.get("/api/v1/strategy/last-ingested-session")
    assert response.status_code == 404
