"""Unit tests for workers/prediction_worker.py.

mock_db_session (AsyncMock spec'd to AsyncSession) stands in for the DB; the real
fakeredis fixture stands in for Redis. _build_race_state's own cumulative-time lookup
is a single batched GROUP BY query (not a per-driver call to _cumulative_race_time —
see Day 35's N+1 fix), so it's mocked the same way as the other db.execute() calls in
this file rather than monkeypatched; _cumulative_race_time itself (still used by
_resolve_position_context, unrelated to _build_race_state) is covered indirectly by
strategy_service's equivalent tests.
"""

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis as fakeredis_lib
import joblib
import numpy as np
import pytest

from backend.services.ml import tire_deg_model
from backend.workers import prediction_worker


@pytest.mark.unit
async def test_build_race_state_batches_cumulative_time_into_one_query(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """cumulative_race_time_seconds for every driver must come from a single batched
    GROUP BY query anchored to current_lap, not one db.execute() call per driver (the
    N+1 this function previously had: ~20 separate round trips for a 20-driver field).
    Also covers the original current_lap-anchoring invariant this test predates: driver
    A's latest DB row is ahead of current_lap (56 > 55), driver B's is behind it
    (54 < 55) — the batched query's lap_number <= current_lap filter must apply
    uniformly to both regardless of either driver's own latest lap_number, otherwise
    normal async ingestion skew bakes a fake multi-lap time gap into the simulation's
    starting point.
    """
    session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    requesting_driver_id = uuid.uuid4()
    driver_a_id = uuid.uuid4()
    driver_b_id = uuid.uuid4()
    current_lap = 55
    season, round_number = 2025, 22

    context_result = MagicMock()
    context_result.one.return_value = (circuit_id, season, round_number, "Yas Marina Circuit")

    lap_a = SimpleNamespace(
        driver_id=driver_a_id, lap_number=56, compound="MEDIUM", tyre_age_laps=10, position=1
    )
    lap_b = SimpleNamespace(
        driver_id=driver_b_id, lap_number=54, compound="HARD", tyre_age_laps=20, position=2
    )
    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = [lap_a, lap_b]

    # session_elapsed_seconds (3rd column) is None for both drivers here —
    # this test is specifically about the SUM(lap_time_seconds) batched-
    # query fallback path (cumulative_time_result below), so the elapsed_
    # by_driver-preferred path must fall through for both.
    position_result = MagicMock()
    position_result.all.return_value = [
        (driver_a_id, lap_a.position, None),
        (driver_b_id, lap_b.position, None),
    ]

    # 3rd column is each driver's own median lap_time_seconds through
    # current_lap (percentile_cont(0.5)) — see baseline_lap_time_seconds.
    cumulative_time_result = MagicMock()
    cumulative_time_result.all.return_value = [
        (driver_a_id, 4321.5, 91.2),
        (driver_b_id, 4310.0, 90.5),
    ]

    captured_queries: list[Any] = []

    async def _execute_side_effect(query: Any, *args: Any, **kwargs: Any) -> Any:
        captured_queries.append(query)
        if len(captured_queries) == 1:
            return context_result
        if len(captured_queries) == 2:
            return latest_laps_result
        if len(captured_queries) == 3:
            return position_result
        if len(captured_queries) == 4:
            return cumulative_time_result
        raise AssertionError(f"unexpected extra db.execute call: {query}")

    mock_db_session.execute.side_effect = _execute_side_effect

    # Weather cache hit so _resolve_weather never falls through to an extra db.execute().
    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 40.0, "air_temp": 28.0}),
    )

    race_state = await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        requesting_driver_id,
        current_lap,
        "SOFT",
        3,
        58,
        {},
    )

    # Exactly one query for cumulative time (and baseline_lap_time_seconds,
    # selected in the same query — see item 4's Checkpoint 2) regardless of
    # field size — the N+1 fix this test guards: 4 total db.execute() calls
    # (context, latest_laps, position, cumulative_time), never one more per
    # driver, and no extra query added for the baseline either.
    assert len(captured_queries) == 4

    cumulative_time_query = captured_queries[3]
    compiled = str(cumulative_time_query.compile(compile_kwargs={"literal_binds": True}))
    # Targeted on the "lap_number <= N" clause specifically, not a bare substring
    # check on the numbers themselves — a bare `str(current_lap) in compiled` (or
    # the drivers' own lap numbers) is a false-negative trap: session_id/driver_id
    # UUIDs are also in this compiled SQL (literal_binds renders them inline) and
    # can coincidentally contain the same digits as a substring.
    assert f"lap_number <= {current_lap}" in compiled
    assert f"lap_number <= {lap_a.lap_number}" not in compiled
    assert f"lap_number <= {lap_b.lap_number}" not in compiled
    assert "percentile_cont" in compiled

    driver_a_state = next(d for d in race_state.drivers if d.driver_id == str(driver_a_id))
    driver_b_state = next(d for d in race_state.drivers if d.driver_id == str(driver_b_id))
    assert driver_a_state.cumulative_race_time_seconds == 4321.5
    assert driver_b_state.cumulative_race_time_seconds == 4310.0
    assert driver_a_state.baseline_lap_time_seconds == pytest.approx(91.2)
    assert driver_b_state.baseline_lap_time_seconds == pytest.approx(90.5)


@pytest.mark.unit
async def test_build_race_state_starting_position_uses_current_lap_not_final_position(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """starting_position must reflect the driver's position AT current_lap, not
    their absolute-latest DB row's position — for a completed session (or any
    session where ingestion has continued past current_lap), that latest row is
    the FINAL classification position, not the position at the point the what-if
    actually starts (the bug this test guards against: the driver's lap 58 row
    shows position=9, but their real position at lap 55 was 10 — starting_position
    must come out as 10, not 9).
    """
    session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    current_lap = 55
    season, round_number = 2025, 22

    context_result = MagicMock()
    context_result.one.return_value = (circuit_id, season, round_number, "Yas Marina Circuit")

    # This driver's absolute-latest DB row is the race's final lap (58), where
    # they classified P9 — but at lap 55 (current_lap) they were P10.
    final_lap = SimpleNamespace(
        driver_id=driver_id, lap_number=58, compound="MEDIUM", tyre_age_laps=17, position=9
    )
    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = [final_lap]

    position_result = MagicMock()
    position_result.all.return_value = [(driver_id, 10, None)]

    cumulative_time_result = MagicMock()
    cumulative_time_result.all.return_value = [(driver_id, 0.0, 88.0)]

    mock_db_session.execute.side_effect = [
        context_result,
        latest_laps_result,
        position_result,
        cumulative_time_result,
    ]

    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 40.0, "air_temp": 28.0}),
    )

    race_state = await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        driver_id,
        current_lap,
        "MEDIUM",
        14,
        58,
        {},
    )

    driver_state = next(d for d in race_state.drivers if d.driver_id == str(driver_id))
    assert driver_state.starting_position == 10
    assert driver_state.starting_position != final_lap.position


@pytest.mark.unit
async def test_build_race_state_position_query_filters_by_session_id(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """The position-as-of-current_lap query must filter by session_id on the
    outer select, not just inside its subquery — otherwise (the regression this
    test guards against) the join matches ANY session's LapData row sharing
    (driver_id, lap_number), which is a real collision: this codebase's
    multi-season training corpus means the same driver commonly has a row at
    the same lap_number in unrelated sessions, each with a different position.

    Simulates driver X with a lap_number=55 row in the target session
    (position=10, the value that must be used) and a different session
    (other_session_id, position=99, what an unfiltered join could accidentally
    pick up instead) — asserts the constructed query's WHERE clause actually
    filters on session_id (proving the SQL-level fix, not just the mocked
    result), and that _build_race_state threads the correct value through.
    """
    session_id = uuid.uuid4()
    other_session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    current_lap = 55
    season, round_number = 2025, 22

    context_result = MagicMock()
    context_result.one.return_value = (circuit_id, season, round_number, "Yas Marina Circuit")

    latest_lap = SimpleNamespace(
        driver_id=driver_id, lap_number=55, compound="MEDIUM", tyre_age_laps=14, position=10
    )
    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = [latest_lap]

    # What a correctly session_id-filtered query returns from a real DB — the
    # other_session_id=99 row is excluded by Postgres, not filtered by this mock.
    position_result = MagicMock()
    position_result.all.return_value = [(driver_id, 10, None)]

    cumulative_time_result = MagicMock()
    cumulative_time_result.all.return_value = [(driver_id, 0.0, 88.0)]

    captured_queries: list[Any] = []

    async def _execute_side_effect(query: Any, *args: Any, **kwargs: Any) -> Any:
        captured_queries.append(query)
        if len(captured_queries) == 1:
            return context_result
        if len(captured_queries) == 2:
            return latest_laps_result
        if len(captured_queries) == 3:
            return position_result
        if len(captured_queries) == 4:
            return cumulative_time_result
        raise AssertionError(f"unexpected extra db.execute call: {query}")

    mock_db_session.execute.side_effect = _execute_side_effect

    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 40.0, "air_temp": 28.0}),
    )

    race_state = await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        driver_id,
        current_lap,
        "MEDIUM",
        14,
        58,
        {},
    )

    position_query = captured_queries[2]
    assert position_query.whereclause is not None, (
        "position_query must filter by session_id on the outer select — without "
        "it, the join matches this driver's lap_number row in ANY session, not "
        "just this one"
    )
    compiled = str(position_query.whereclause.compile(compile_kwargs={"literal_binds": True}))
    assert session_id.hex in compiled.replace("-", "")
    assert other_session_id.hex not in compiled.replace("-", "")

    driver_state = next(d for d in race_state.drivers if d.driver_id == str(driver_id))
    assert driver_state.starting_position == 10


# --- _load_models: WET/INTER schema-mismatch alias (Checkpoint 3) ---
# See docs/simulator-issues-wet-model-and-position-context.md Part A. Mirrors
# test_strategy_service.py's identical wiring test — this module has its own
# duplicated _load_models (see this file's module docstring on the
# no-cross-service-import convention), so it needs its own coverage.


def _fit_pipeline_with_n_features(n_features: int, seed: int) -> Any:
    rng = np.random.default_rng(seed)
    n = 60
    features = rng.random((n, n_features))
    target = rng.normal(0.0, 0.3, n)
    pipeline = tire_deg_model._build_pipeline()
    pipeline.fit(features, target)
    return pipeline


@pytest.mark.unit
def test_load_models_aliases_schema_incompatible_wet_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_wet = _fit_pipeline_with_n_features(n_features=8, seed=300)
    inter = _fit_pipeline_with_n_features(n_features=len(tire_deg_model.FEATURE_COLUMNS), seed=301)
    other = _fit_pipeline_with_n_features(n_features=len(tire_deg_model.FEATURE_COLUMNS), seed=302)
    pipelines_by_filename = {"tire_deg_wet.pkl": stale_wet, "tire_deg_inter.pkl": inter}

    monkeypatch.setattr(prediction_worker, "_download_from_s3", lambda filename: filename)
    # Patches the joblib module itself (not prediction_worker.joblib) — both
    # reference the same module object, and reaching through another
    # module's imported attribute trips mypy --strict's --no-implicit-reexport.
    monkeypatch.setattr(joblib, "load", lambda path: pipelines_by_filename.get(path, other))
    monkeypatch.setattr(prediction_worker, "_model_cache", {})
    # No sidecar for any filename here — this test is scoped to model aliasing
    # only; the encoding-maps side of the same aliasing call is covered by
    # test_load_models_aliases_encoding_maps_alongside_wet_pipeline below.
    monkeypatch.setattr(prediction_worker, "_download_metrics_from_s3", lambda filename: None)
    monkeypatch.setattr(prediction_worker, "_encoding_maps_cache", {})

    models = prediction_worker._load_models()

    assert models["tire_deg_wet.pkl"] is inter
    assert models["tire_deg_inter.pkl"] is inter
    assert set(models) == set(prediction_worker._MODEL_FILES)


@pytest.mark.unit
def test_load_models_populates_encoding_maps_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """_load_models() downloads each tire_deg model's own sidecar and parses its encoding maps."""
    n_features = len(tire_deg_model.FEATURE_COLUMNS)
    pipeline = _fit_pipeline_with_n_features(n_features=n_features, seed=310)
    metrics_by_filename = {
        "tire_deg_soft.pkl": {
            "holdout_mae": 0.5,
            "driver_id_to_code": {"d1": 3},
            "circuit_name_to_code": {"Monza": 7},
        },
        "tire_deg_medium.pkl": None,  # legacy sidecar — predates this fix, no maps recorded
    }

    monkeypatch.setattr(prediction_worker, "_download_from_s3", lambda filename: filename)
    monkeypatch.setattr(joblib, "load", lambda path: pipeline)
    monkeypatch.setattr(
        prediction_worker,
        "_download_metrics_from_s3",
        lambda filename: metrics_by_filename.get(filename),
    )
    monkeypatch.setattr(prediction_worker, "_model_cache", {})
    monkeypatch.setattr(prediction_worker, "_encoding_maps_cache", {})

    prediction_worker._load_models()
    maps = prediction_worker._load_encoding_maps()

    assert maps["tire_deg_soft.pkl"] == tire_deg_model.CategoricalEncodingMaps(
        driver_id_to_code={"d1": 3}, circuit_name_to_code={"Monza": 7}
    )
    assert maps["tire_deg_medium.pkl"] is None
    assert "pit_predictor.pkl" not in maps  # only tire_deg_* filenames get an entry


@pytest.mark.unit
def test_load_models_aliases_encoding_maps_alongside_wet_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WET's model-schema alias also aliases its encoding-maps cache entry to INTER's real map."""
    stale_wet = _fit_pipeline_with_n_features(n_features=8, seed=311)
    inter = _fit_pipeline_with_n_features(n_features=len(tire_deg_model.FEATURE_COLUMNS), seed=312)
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

    monkeypatch.setattr(prediction_worker, "_download_from_s3", lambda filename: filename)
    monkeypatch.setattr(joblib, "load", lambda path: pipelines_by_filename.get(path, inter))
    monkeypatch.setattr(
        prediction_worker,
        "_download_metrics_from_s3",
        lambda filename: metrics_by_filename.get(filename),
    )
    monkeypatch.setattr(prediction_worker, "_model_cache", {})
    monkeypatch.setattr(prediction_worker, "_encoding_maps_cache", {})

    prediction_worker._load_models()
    maps = prediction_worker._load_encoding_maps()

    assert maps["tire_deg_wet.pkl"] == tire_deg_model.CategoricalEncodingMaps(
        driver_id_to_code={"d1": 1}, circuit_name_to_code={"Monza": 2}
    )
    assert maps["tire_deg_wet.pkl"] is maps["tire_deg_inter.pkl"]


@pytest.mark.unit
async def test_cumulative_race_time_prefers_session_elapsed_seconds(
    mock_db_session: AsyncMock,
) -> None:
    """Mirrors strategy_service's equivalent test — same duplicated-function
    convention as _cumulative_race_time itself. A backfilled historical
    session's real session_elapsed_seconds must be returned directly; the
    SUM(lap_time_seconds) fallback query must never even run.
    """
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()

    latest_row_result = MagicMock()
    latest_row_result.scalar_one_or_none.return_value = 5231.627
    mock_db_session.execute.return_value = latest_row_result

    result = await prediction_worker._cumulative_race_time(
        mock_db_session, session_id, driver_id, 52
    )

    assert result == pytest.approx(5231.627)
    mock_db_session.execute.assert_called_once()


@pytest.mark.unit
async def test_cumulative_race_time_falls_back_to_sum_when_session_elapsed_seconds_null(
    mock_db_session: AsyncMock,
) -> None:
    """A live-ingested session (never backfilled) must fall back to the
    original SUM(lap_time_seconds) reconstruction, unchanged."""
    session_id = uuid.uuid4()
    driver_id = uuid.uuid4()

    latest_row_result = MagicMock()
    latest_row_result.scalar_one_or_none.return_value = None
    sum_result = MagicMock()
    sum_result.scalar_one.return_value = 4310.0
    mock_db_session.execute.side_effect = [latest_row_result, sum_result]

    result = await prediction_worker._cumulative_race_time(
        mock_db_session, session_id, driver_id, 40
    )

    assert result == pytest.approx(4310.0)
    assert mock_db_session.execute.call_count == 2


@pytest.mark.unit
async def test_build_race_state_prefers_session_elapsed_seconds_over_sum_fallback(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """When the position-as-of-current_lap row carries a real
    session_elapsed_seconds (a backfilled historical session), it must be
    used for cumulative_race_time_seconds instead of the batched
    SUM(lap_time_seconds) query's value — even though both are present on
    the same row, chosen here to be clearly distinguishable (900.5 vs 1.0).
    """
    session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    current_lap = 52
    season, round_number = 2026, 9

    context_result = MagicMock()
    context_result.one.return_value = (circuit_id, season, round_number, "Silverstone Circuit")

    lap = SimpleNamespace(
        driver_id=driver_id, lap_number=52, compound="MEDIUM", tyre_age_laps=20, position=1
    )
    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = [lap]

    position_result = MagicMock()
    position_result.all.return_value = [(driver_id, 1, 900.5)]

    # Present but must be ignored in favour of the 900.5 above.
    cumulative_time_result = MagicMock()
    cumulative_time_result.all.return_value = [(driver_id, 1.0, 88.0)]

    mock_db_session.execute.side_effect = [
        context_result,
        latest_laps_result,
        position_result,
        cumulative_time_result,
    ]

    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 30.0, "air_temp": 22.0}),
    )

    race_state = await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        driver_id,
        current_lap,
        "MEDIUM",
        20,
        52,
        {},
    )

    driver_state = next(d for d in race_state.drivers if d.driver_id == str(driver_id))
    assert driver_state.cumulative_race_time_seconds == pytest.approx(900.5)


# --- baseline_lap_time_seconds (item 4: predicted_finish_time should be a real
# absolute elapsed time, not just an accumulated delta) ---


@pytest.mark.unit
async def test_build_race_state_missing_baseline_falls_back_to_field_median(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """A driver with zero valid timed laps through current_lap (no median of
    their own — the cumulative_time_query's 3rd column is None for them) must
    get the field's own median baseline_lap_time_seconds, not 0.0 — 0.0 would
    give them an artificial ~0s/lap pace and rank them P1 in the simulation
    regardless of their real position.
    """
    session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    driver_a_id = uuid.uuid4()
    driver_b_id = uuid.uuid4()
    driver_c_id = uuid.uuid4()
    current_lap = 30
    season, round_number = 2026, 10

    context_result = MagicMock()
    context_result.one.return_value = (
        circuit_id,
        season,
        round_number,
        "Circuit de Spa-Francorchamps",
    )

    lap_a = SimpleNamespace(
        driver_id=driver_a_id, lap_number=30, compound="MEDIUM", tyre_age_laps=10, position=1
    )
    lap_b = SimpleNamespace(
        driver_id=driver_b_id, lap_number=30, compound="MEDIUM", tyre_age_laps=10, position=2
    )
    lap_c = SimpleNamespace(
        driver_id=driver_c_id, lap_number=30, compound="MEDIUM", tyre_age_laps=10, position=3
    )
    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = [lap_a, lap_b, lap_c]

    position_result = MagicMock()
    position_result.all.return_value = [
        (driver_a_id, 1, None),
        (driver_b_id, 2, None),
        (driver_c_id, 3, None),
    ]

    # driver_c has NO median (3rd column None) — unlike driver_a/driver_b's
    # real values, e.g. every one of their laps through current_lap was an
    # out-lap/in-lap/SC lap with a NULL lap_time_seconds.
    cumulative_time_result = MagicMock()
    cumulative_time_result.all.return_value = [
        (driver_a_id, 2700.0, 90.0),
        (driver_b_id, 2760.0, 92.0),
        (driver_c_id, 0.0, None),
    ]

    mock_db_session.execute.side_effect = [
        context_result,
        latest_laps_result,
        position_result,
        cumulative_time_result,
    ]

    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 25.0, "air_temp": 18.0}),
    )

    race_state = await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        driver_a_id,
        current_lap,
        "MEDIUM",
        10,
        44,
        {},
    )

    driver_a_state = next(d for d in race_state.drivers if d.driver_id == str(driver_a_id))
    driver_b_state = next(d for d in race_state.drivers if d.driver_id == str(driver_b_id))
    driver_c_state = next(d for d in race_state.drivers if d.driver_id == str(driver_c_id))
    assert driver_a_state.baseline_lap_time_seconds == pytest.approx(90.0)
    assert driver_b_state.baseline_lap_time_seconds == pytest.approx(92.0)
    # median([90.0, 92.0]) = 91.0 — the field median, not either individual
    # driver's own value, and not 0.0.
    assert driver_c_state.baseline_lap_time_seconds == pytest.approx(91.0)


@pytest.mark.unit
async def test_build_race_state_baseline_zero_when_field_has_none(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """When NO driver in the field has any median yet (e.g. a genuine pre-race
    what-if with zero ingested lap_data), baseline_lap_time_seconds collapses
    to 0.0 for the requester — ranking-neutral, identical to the pre-baseline
    behaviour, rather than favouring any one driver.
    """
    session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    requesting_driver_id = uuid.uuid4()
    season, round_number = 2026, 12

    context_result = MagicMock()
    context_result.one.return_value = (circuit_id, season, round_number, "Circuit Zandvoort")

    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = []

    position_result = MagicMock()
    position_result.all.return_value = []

    cumulative_time_result = MagicMock()
    cumulative_time_result.all.return_value = []

    mock_db_session.execute.side_effect = [
        context_result,
        latest_laps_result,
        position_result,
        cumulative_time_result,
    ]

    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 22.0, "air_temp": 17.0}),
    )

    race_state = await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        requesting_driver_id,
        1,
        "SOFT",
        0,
        50,
        {},
    )

    driver_state = next(d for d in race_state.drivers if d.driver_id == str(requesting_driver_id))
    assert driver_state.baseline_lap_time_seconds == 0.0
