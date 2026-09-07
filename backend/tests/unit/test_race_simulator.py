"""Unit tests for services/ml/race_simulator.py's simulate_race.

All three underlying models (tire_deg pipelines, pit classifier, safety car model)
are synthetic, fit on random/constructed data in-fixture — no real .pkl files, no
DB, no network. Marked @pytest.mark.slow: simulate_race's inner loop is @numba.njit
and the FIRST call in a process pays a one-off JIT compile cost (30-60s) — expected,
not a test failure. Kept in the default unit suite (no addopts filter excludes
`slow` in this repo) since race_simulator.py needs its own coverage for the >80%
services/ target; use `-m "not slow"` to skip it if that JIT cost becomes disruptive.
"""

import uuid
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from backend.services.ml import tire_deg_model
from backend.services.ml.pit_predictor import FEATURE_COLUMNS as PIT_FEATURE_COLUMNS
from backend.services.ml.pit_predictor import _build_model
from backend.services.ml.race_simulator import (
    SC_LAP_TIME_MULTIPLIER,
    DriverPositionDistribution,
    DriverRaceState,
    RaceSimulationInput,
    RaceSimulationResult,
    _advance_lap,
    _tire_deg_predictions,
    simulate_race,
)
from backend.services.ml.safety_car_model import SafetyCarModel
from backend.services.ml.tire_deg_model import FEATURE_COLUMNS as TIRE_FEATURE_COLUMNS
from backend.services.ml.tire_deg_model import MAX_LOOKAHEAD_LAPS, _build_pipeline

CIRCUIT_NAME = "Test Circuit"
COMPOUND = "MEDIUM"
COMPOUND_ENCODED = 2
N_DRIVERS = 4


def _synthetic_tire_pipeline(seed: int) -> Any:
    return _synthetic_tire_pipeline_with_n_features(len(TIRE_FEATURE_COLUMNS), seed)


def _synthetic_tire_pipeline_with_n_features(n_features: int, seed: int) -> Any:
    """Same as _synthetic_tire_pipeline but with a caller-chosen feature count —
    used to simulate a schema-drifted model (see tire_deg_model.pipeline_feature_count).
    """
    rng = np.random.default_rng(seed)
    n = 60
    features = rng.random((n, n_features))
    target = rng.normal(0.0, 0.3, n)
    pipeline = _build_pipeline()
    pipeline.fit(features, target)
    return pipeline


def _synthetic_pit_model(seed: int) -> Any:
    rng = np.random.default_rng(seed)
    n = 100
    features = rng.random((n, len(PIT_FEATURE_COLUMNS)))
    target = rng.integers(0, 2, n)
    model = _build_model(scale_pos_weight=1.0)
    model.fit(features, target)
    return model


@pytest.fixture
def race_state() -> RaceSimulationInput:
    drivers = [
        DriverRaceState(
            driver_id=str(uuid.uuid4()),
            starting_position=i + 1,
            compound=COMPOUND,
            compound_encoded=COMPOUND_ENCODED,
            tyre_age_laps=5 + i,
            driver_id_encoded=i,
            cumulative_race_time_seconds=100.0 * i,
        )
        for i in range(N_DRIVERS)
    ]
    return RaceSimulationInput(
        circuit_name=CIRCUIT_NAME,
        circuit_id_encoded=0,
        current_lap=45,
        total_laps=48,  # only 3 laps to simulate — keeps the 1000-sim run fast
        wet_track=False,
        track_temp=30.0,
        air_temp=20.0,
        drivers=drivers,
    )


@pytest.fixture
def tire_deg_pipelines() -> dict[str, Any]:
    return {COMPOUND: _synthetic_tire_pipeline(seed=1)}


@pytest.fixture
def pit_model() -> Any:
    return _synthetic_pit_model(seed=2)


@pytest.fixture
def sc_model() -> SafetyCarModel:
    return SafetyCarModel(circuit_rates={CIRCUIT_NAME: 0.001}, default_rate=0.001)


# --- _tire_deg_predictions schema-drift backstop ---
# Covers docs/simulator-issues-wet-model-and-position-context.md Part A's
# recommendation 2a: a model-shape mismatch or a raising predict() must
# degrade only that compound group, never crash the whole simulation. These
# call _tire_deg_predictions directly (no numba path involved) so they run
# in milliseconds — not marked @slow, unlike the full simulate_race tests
# below.


def _tire_deg_predictions_inputs(
    race_state: RaceSimulationInput, n_sims: int = 2
) -> dict[str, Any]:
    n_drivers = len(race_state.drivers)
    return {
        "race_state": race_state,
        "compound_groups": {COMPOUND: np.arange(n_drivers)},
        "compound_encoded_by_driver": np.array(
            [d.compound_encoded for d in race_state.drivers], dtype=np.int64
        ),
        "lap_number": race_state.current_lap + 1,
        "tyre_age": np.tile(
            np.array([d.tyre_age_laps for d in race_state.drivers], dtype=np.int64),
            (n_sims, 1),
        ),
        "driver_id_encoded": np.array(
            [d.driver_id_encoded for d in race_state.drivers], dtype=np.int64
        ),
        "fuel_adjusted_time": 0.0,
    }


@pytest.mark.unit
def test_tire_deg_predictions_skips_mismatched_pipeline_without_raising(
    race_state: RaceSimulationInput,
) -> None:
    n_sims = 2
    inputs = _tire_deg_predictions_inputs(race_state, n_sims=n_sims)
    mismatched_pipeline = _synthetic_tire_pipeline_with_n_features(
        n_features=len(TIRE_FEATURE_COLUMNS) + 2, seed=20
    )

    predicted_delta, predicted_life_remaining = _tire_deg_predictions(
        tire_deg_pipelines={COMPOUND: mismatched_pipeline}, **inputs
    )

    assert np.all(predicted_delta == 0.0)
    assert np.all(predicted_life_remaining == float(MAX_LOOKAHEAD_LAPS))


@pytest.mark.unit
def test_tire_deg_predictions_skips_pipeline_that_raises_on_predict(
    race_state: RaceSimulationInput,
) -> None:
    inputs = _tire_deg_predictions_inputs(race_state)
    raising_pipeline = MagicMock()
    raising_pipeline.named_steps = {"scaler": MagicMock(n_features_in_=len(TIRE_FEATURE_COLUMNS))}
    raising_pipeline.predict.side_effect = ValueError("boom")

    predicted_delta, predicted_life_remaining = _tire_deg_predictions(
        tire_deg_pipelines={COMPOUND: raising_pipeline}, **inputs
    )

    assert np.all(predicted_delta == 0.0)
    assert np.all(predicted_life_remaining == float(MAX_LOOKAHEAD_LAPS))


@pytest.mark.unit
def test_tire_deg_predictions_uses_compatible_pipeline_normally(
    race_state: RaceSimulationInput,
) -> None:
    inputs = _tire_deg_predictions_inputs(race_state)
    compatible_pipeline = _synthetic_tire_pipeline_with_n_features(
        n_features=len(TIRE_FEATURE_COLUMNS), seed=21
    )

    predicted_delta, _predicted_life_remaining = _tire_deg_predictions(
        tire_deg_pipelines={COMPOUND: compatible_pipeline}, **inputs
    )

    # A real (fitted-on-random-data) pipeline should not trivially produce
    # every value at exactly the degrade-gracefully default (predicted_life_
    # remaining legitimately caps at MAX_LOOKAHEAD_LAPS for a model that never
    # crosses the degradation threshold within the lookahead window, so that
    # field alone can't distinguish "used normally" from "skipped" — delta can).
    assert not np.all(predicted_delta == 0.0)


# --- _tire_deg_predictions memoization correctness (What-If Simulator
# multi-scenario rebuild, Checkpoint 1: see race_simulator.py's own
# docstring on _tire_deg_predictions for the ~53s -> ~14s measurement this
# is verifying is exact, not an approximation) ---


@pytest.mark.unit
def test_tire_deg_predictions_dedup_matches_naive_predictions(
    race_state: RaceSimulationInput,
) -> None:
    """Deduping on (tyre_age, driver_id_encoded, compound_encoded) must be
    bit-identical to predicting every (sim, driver) row directly — a fitted
    pipeline's predict() is a deterministic, stateless function of its input
    row, so grouping by unique input and scattering the result back can
    never change the answer, only how many rows reach the model.

    Builds a tyre_age array with a genuine mix of repeated AND unique values
    across sims (not the fixture's default all-identical-across-sims
    tiling), so this actually exercises non-trivial deduplication rather
    than a degenerate single-unique-row case.
    """
    n_sims = 37  # odd, deliberately not a multiple of n_drivers — no accidental alignment
    n_drivers = len(race_state.drivers)
    pipeline = _synthetic_tire_pipeline(seed=42)

    rng = np.random.default_rng(7)
    tyre_age = rng.integers(0, 30, size=(n_sims, n_drivers)).astype(np.int64)
    driver_id_encoded = np.array([d.driver_id_encoded for d in race_state.drivers], dtype=np.int64)
    compound_encoded_by_driver = np.array(
        [d.compound_encoded for d in race_state.drivers], dtype=np.int64
    )
    lap_number = race_state.current_lap + 1
    fuel_adjusted_time = -1.5
    compound_groups = {COMPOUND: np.arange(n_drivers)}

    predicted_delta, predicted_life_remaining = _tire_deg_predictions(
        race_state=race_state,
        compound_groups=compound_groups,
        compound_encoded_by_driver=compound_encoded_by_driver,
        tire_deg_pipelines={COMPOUND: pipeline},
        lap_number=lap_number,
        tyre_age=tyre_age,
        driver_id_encoded=driver_id_encoded,
        fuel_adjusted_time=fuel_adjusted_time,
    )

    # Ground truth: the pre-memoization approach — build the full
    # (n_sims * n_drivers)-row feature matrix with no deduplication at all
    # and predict on every row directly.
    tyre_age_flat = tyre_age.ravel().astype(np.int64)
    compound_encoded_flat = np.tile(compound_encoded_by_driver, n_sims)
    driver_id_encoded_flat = np.tile(driver_id_encoded, n_sims)
    naive_features = np.column_stack(
        [
            np.full(tyre_age_flat.shape[0], lap_number, dtype=np.float64),
            compound_encoded_flat.astype(np.float64),
            tyre_age_flat.astype(np.float64),
            np.full(tyre_age_flat.shape[0], fuel_adjusted_time),
            np.full(tyre_age_flat.shape[0], race_state.circuit_id_encoded, dtype=np.float64),
            driver_id_encoded_flat.astype(np.float64),
        ]
    )
    naive_delta = pipeline.predict(naive_features).reshape(tyre_age.shape)
    naive_life = tire_deg_model.predict_life_remaining_batch(
        pipeline,
        np.full(tyre_age_flat.shape[0], lap_number, dtype=np.int64),
        compound_encoded_flat,
        tyre_age_flat,
        np.full(tyre_age_flat.shape[0], fuel_adjusted_time),
        np.full(tyre_age_flat.shape[0], race_state.circuit_id_encoded, dtype=np.int64),
        driver_id_encoded_flat,
    ).reshape(tyre_age.shape)

    # Sanity check this test actually exercises deduplication, not a
    # degenerate all-unique or all-identical case.
    n_unique_tyre_ages = len(np.unique(tyre_age))
    assert 1 < n_unique_tyre_ages < tyre_age.size

    np.testing.assert_array_equal(predicted_delta, naive_delta)
    np.testing.assert_array_equal(predicted_life_remaining, naive_life)


# --- _advance_lap baseline_lap_time_seconds handling (item 4: predicted_finish_time
# should be a real absolute elapsed time, not just an accumulated delta) ---
# Direct, deterministic tests of the numba inner loop (noise_std=0.0) — exact
# arithmetic can be asserted without a Monte Carlo run.


@pytest.mark.unit
@pytest.mark.slow
def test_advance_lap_adds_baseline_on_a_racing_lap() -> None:
    cumulative_time = np.array([[1000.0, 2000.0]])
    tyre_age = np.array([[5, 5]], dtype=np.int64)
    predicted_delta = np.array([[0.0, 0.0]])
    baseline_lap_time = np.array([50.0, 80.0])
    pit_flags = np.array([[False, False]])
    sc_active = np.array([False])

    _advance_lap(
        cumulative_time,
        tyre_age,
        predicted_delta,
        baseline_lap_time,
        0.0,  # noise_std
        pit_flags,
        22.0,  # pit_stop_seconds
        sc_active,
        999.0,  # sc_lap_time_seconds — irrelevant, sc not active this lap
    )

    assert cumulative_time[0, 0] == pytest.approx(1050.0)
    assert cumulative_time[0, 1] == pytest.approx(2080.0)
    assert tyre_age[0, 0] == 6
    assert tyre_age[0, 1] == 6


@pytest.mark.unit
@pytest.mark.slow
def test_advance_lap_ignores_baseline_on_an_sc_lap() -> None:
    """sc_lap_time_seconds is already a real absolute lap time in its own right
    (see simulate_race's derivation) — per-driver baseline_lap_time_seconds must
    NOT also be added on top during the SC-bunching branch.
    """
    cumulative_time = np.array([[1000.0, 1200.0]])
    tyre_age = np.array([[5, 5]], dtype=np.int64)
    predicted_delta = np.array([[0.0, 0.0]])
    baseline_lap_time = np.array([50.0, 999999.0])  # wildly different — must not matter
    pit_flags = np.array([[False, False]])
    sc_active = np.array([True])

    _advance_lap(
        cumulative_time,
        tyre_age,
        predicted_delta,
        baseline_lap_time,
        0.0,
        pit_flags,
        22.0,
        sc_active,
        30.0,  # sc_lap_time_seconds
    )

    expected = 1000.0 + 30.0  # leader_time (min of the two) + sc_lap_time_seconds
    assert cumulative_time[0, 0] == pytest.approx(expected)
    assert cumulative_time[0, 1] == pytest.approx(expected)


@pytest.mark.unit
@pytest.mark.slow
def test_simulate_race_completes_with_one_mismatched_compound(
    tire_deg_pipelines: dict[str, Any],
    pit_model: Any,
    sc_model: SafetyCarModel,
) -> None:
    """A mixed field — one driver on a schema-drifted compound, one on a healthy
    one — must still produce a full result for both drivers; only the drifted
    compound's predictions degrade to the safe defaults inside the loop.
    """
    drivers = [
        DriverRaceState(
            driver_id="healthy-driver",
            starting_position=1,
            compound=COMPOUND,
            compound_encoded=COMPOUND_ENCODED,
            tyre_age_laps=5,
            driver_id_encoded=0,
            cumulative_race_time_seconds=0.0,
        ),
        DriverRaceState(
            driver_id="drifted-driver",
            starting_position=2,
            compound="WET",
            compound_encoded=4,
            tyre_age_laps=3,
            driver_id_encoded=1,
            cumulative_race_time_seconds=10.0,
        ),
    ]
    race_state_with_mismatch = RaceSimulationInput(
        circuit_name=CIRCUIT_NAME,
        circuit_id_encoded=0,
        current_lap=45,
        total_laps=48,
        wet_track=True,
        track_temp=15.0,
        air_temp=12.0,
        drivers=drivers,
    )
    mismatched_pipelines = {
        **tire_deg_pipelines,
        "WET": _synthetic_tire_pipeline_with_n_features(n_features=8, seed=22),
    }

    result = simulate_race(
        race_state_with_mismatch, mismatched_pipelines, pit_model, sc_model, rng_seed=42
    )

    assert result.n_simulations == 1000
    assert len(result.driver_distributions) == 2
    for distribution in result.driver_distributions:
        assert sum(distribution.position_probabilities.values()) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.unit
@pytest.mark.slow
def test_returns_1000_outcomes(
    race_state: RaceSimulationInput,
    tire_deg_pipelines: dict[str, Any],
    pit_model: Any,
    sc_model: SafetyCarModel,
) -> None:
    result = simulate_race(race_state, tire_deg_pipelines, pit_model, sc_model, rng_seed=42)
    assert result.n_simulations == 1000
    for distribution in result.driver_distributions:
        assert sum(distribution.position_probabilities.values()) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.unit
@pytest.mark.slow
def test_position_sum_across_drivers_matches_expected(
    race_state: RaceSimulationInput,
    tire_deg_pipelines: dict[str, Any],
    pit_model: Any,
    sc_model: SafetyCarModel,
) -> None:
    result = simulate_race(race_state, tire_deg_pipelines, pit_model, sc_model, rng_seed=7)
    total_mean_position = sum(d.mean_position for d in result.driver_distributions)
    expected = N_DRIVERS * (N_DRIVERS + 1) / 2
    assert total_mean_position == pytest.approx(expected, abs=1e-6)


@pytest.mark.unit
@pytest.mark.slow
def test_seed_produces_reproducible_results(
    race_state: RaceSimulationInput,
    tire_deg_pipelines: dict[str, Any],
    pit_model: Any,
    sc_model: SafetyCarModel,
) -> None:
    first_run = simulate_race(
        race_state, tire_deg_pipelines, pit_model, sc_model, n_simulations=200, rng_seed=99
    )
    second_run = simulate_race(
        race_state, tire_deg_pipelines, pit_model, sc_model, n_simulations=200, rng_seed=99
    )

    for first, second in zip(
        first_run.driver_distributions, second_run.driver_distributions, strict=True
    ):
        assert first.driver_id == second.driver_id
        assert first.mean_position == pytest.approx(second.mean_position)
        assert first.mean_finish_time_seconds == pytest.approx(second.mean_finish_time_seconds)
        assert first.position_probabilities == second.position_probabilities


@pytest.mark.unit
@pytest.mark.slow
def test_forced_pit_laps_changes_outcome_only_for_that_driver(
    race_state: RaceSimulationInput, tire_deg_pipelines: dict[str, Any], sc_model: SafetyCarModel
) -> None:
    """forced_pit_laps bypasses the pit model entirely for the named driver/lap, so
    with a pit model that (almost) never recommends pitting on its own, any pit
    stop in the forced run is attributable to forced_pit_laps — and since
    _advance_lap draws one noise sample per (sim, driver) per lap regardless of
    whether that driver pits, the untouched driver's outcome must be bit-identical
    between the two runs.
    """

    def _no_pit_predict_proba(features: np.ndarray) -> np.ndarray:
        return np.tile([0.95, 0.05], (features.shape[0], 1))

    no_pit_model = MagicMock()
    no_pit_model.predict_proba.side_effect = _no_pit_predict_proba

    forced_driver_id = race_state.drivers[0].driver_id
    other_driver_id = race_state.drivers[1].driver_id
    forced_pit_laps = {forced_driver_id: {46: ("HARD", 0)}}

    baseline = simulate_race(
        race_state, tire_deg_pipelines, no_pit_model, sc_model, n_simulations=100, rng_seed=123
    )
    forced = simulate_race(
        race_state,
        tire_deg_pipelines,
        no_pit_model,
        sc_model,
        n_simulations=100,
        rng_seed=123,
        forced_pit_laps=forced_pit_laps,
    )

    def _finish_time(result: RaceSimulationResult, driver_id: str) -> float:
        return next(
            d.mean_finish_time_seconds
            for d in result.driver_distributions
            if d.driver_id == driver_id
        )

    assert _finish_time(baseline, other_driver_id) == pytest.approx(
        _finish_time(forced, other_driver_id)
    )
    assert _finish_time(baseline, forced_driver_id) != pytest.approx(
        _finish_time(forced, forced_driver_id)
    )


@pytest.mark.unit
@pytest.mark.slow
def test_sc_lap_time_derived_from_field_median_baseline(
    tire_deg_pipelines: dict[str, Any],
) -> None:
    """With SC certain every lap and pitting suppressed, _advance_lap's SC branch
    bunches the whole field to leader_time + sc_lap_time_seconds each lap — so
    starting every driver's cumulative_race_time_seconds at 0.0 makes the final
    mean_finish_time_seconds exactly n_remaining_laps * sc_lap_time_seconds for
    every driver, letting SC_LAP_TIME_MULTIPLIER's effect be checked precisely
    end-to-end through simulate_race rather than only via _advance_lap directly.
    """
    baseline = 90.0
    n_remaining_laps = 3  # current_lap=45, total_laps=48, same as the race_state fixture
    drivers = [
        DriverRaceState(
            driver_id=str(uuid.uuid4()),
            starting_position=i + 1,
            compound=COMPOUND,
            compound_encoded=COMPOUND_ENCODED,
            tyre_age_laps=5,
            driver_id_encoded=i,
            cumulative_race_time_seconds=0.0,
            baseline_lap_time_seconds=baseline,
        )
        for i in range(N_DRIVERS)
    ]
    race_state_with_baseline = RaceSimulationInput(
        circuit_name=CIRCUIT_NAME,
        circuit_id_encoded=0,
        current_lap=45,
        total_laps=48,
        wet_track=False,
        track_temp=30.0,
        air_temp=20.0,
        drivers=drivers,
    )
    always_sc_model = MagicMock()
    always_sc_model.probability_within.return_value = 1.0

    def _no_pit_predict_proba(features: np.ndarray) -> np.ndarray:
        return np.tile([0.95, 0.05], (features.shape[0], 1))

    no_pit_model = MagicMock()
    no_pit_model.predict_proba.side_effect = _no_pit_predict_proba

    result = simulate_race(
        race_state_with_baseline,
        tire_deg_pipelines,
        no_pit_model,
        always_sc_model,
        n_simulations=10,
        rng_seed=1,
    )

    expected_finish_time = n_remaining_laps * (baseline * SC_LAP_TIME_MULTIPLIER)
    for distribution in result.driver_distributions:
        assert distribution.mean_finish_time_seconds == pytest.approx(
            expected_finish_time, abs=1e-6
        )


# --- projected_pit_laps / finish_ahead_probability (What-If Simulator
# rebuild part (b): see docs/core-feature-rebuild-whatif-simulator.md §7) ---


@pytest.mark.unit
def test_driver_position_distribution_defaults_new_fields_when_omitted() -> None:
    """Every pre-existing DriverPositionDistribution(...) call site (real code
    and other test fixtures in this suite) constructs one without
    projected_pit_laps/finish_ahead_probability — both must default to empty,
    not require every call site to be updated for this fix.
    """
    distribution = DriverPositionDistribution(
        driver_id="driver-1",
        position_probabilities={1: 1.0},
        mean_position=1.0,
        mean_finish_time_seconds=5400.0,
        finish_time_p5_seconds=5350.0,
        finish_time_p95_seconds=5460.0,
    )
    assert distribution.projected_pit_laps == []
    assert distribution.finish_ahead_probability == {}


@pytest.mark.unit
@pytest.mark.slow
def test_projected_pit_laps_reflects_forced_pit_lap(
    race_state: RaceSimulationInput, tire_deg_pipelines: dict[str, Any], sc_model: SafetyCarModel
) -> None:
    """A forced what-if pit lap must show probability 1.0 at that lap for the
    requesting driver — projected_pit_laps is captured from the SAME pit_flags
    array that actually fires the pit stop (see simulate_race's own comment on
    this), not a separate computation that could disagree with it. Another
    driver, with a pit model that (almost) never recommends pitting on its own
    (same no_pit_model as test_forced_pit_laps_changes_outcome_only_for_that_
    driver), must show an empty projected_pit_laps — sparse, probability > 0.0
    only, same convention as position_probabilities' own shaping.
    """

    def _no_pit_predict_proba(features: np.ndarray) -> np.ndarray:
        return np.tile([0.95, 0.05], (features.shape[0], 1))

    no_pit_model = MagicMock()
    no_pit_model.predict_proba.side_effect = _no_pit_predict_proba

    forced_driver_id = race_state.drivers[0].driver_id
    other_driver_id = race_state.drivers[1].driver_id
    forced_pit_laps = {forced_driver_id: {46: ("HARD", 0)}}

    result = simulate_race(
        race_state,
        tire_deg_pipelines,
        no_pit_model,
        sc_model,
        n_simulations=100,
        rng_seed=123,
        forced_pit_laps=forced_pit_laps,
    )

    forced_dist = next(d for d in result.driver_distributions if d.driver_id == forced_driver_id)
    other_dist = next(d for d in result.driver_distributions if d.driver_id == other_driver_id)

    assert forced_dist.projected_pit_laps == [(46, 1.0)]
    assert other_dist.projected_pit_laps == []


@pytest.mark.unit
@pytest.mark.slow
def test_finish_ahead_probability_complementary_and_matches_large_gap(
    race_state: RaceSimulationInput,
    tire_deg_pipelines: dict[str, Any],
    pit_model: Any,
) -> None:
    """finish_ahead_probability must (a) reflect a real, large starting gap and
    (b) be internally consistent: P(i ahead of j) + P(j ahead of i) == 1.0, since
    both come from the SAME final cumulative_time array position_probabilities
    is built from, not two independent estimates.

    SC is force-disabled here (a MagicMock returning probability 0.0, not the
    sc_model fixture's tiny-but-nonzero rate): an SC lap on the FINAL simulated
    lap bunches every driver in that sim to the identical leader_time-derived
    value (see _advance_lap), which can produce a genuine EXACT tie between two
    drivers — contributing to neither direction and making (b)'s sum come out
    slightly UNDER 1.0 (confirmed directly: ~0.998 over 1000 sims at the
    sc_model fixture's real 0.001 rate, an accurate reflection of ~2 tied sims,
    not a bug — see finish_ahead_probability's own docstring). Eliminating SC
    isolates the property this test is actually about.

    The race_state fixture starts driver i's cumulative_race_time_seconds at
    100.0 * i — driver 0 leads driver 3 by 300s with only 3 laps remaining
    (current_lap=45, total_laps=48), far too large a gap for per-lap noise
    (LAP_TIME_NOISE_STD_SECONDS=0.35) to plausibly close. Driver 0 must finish
    ahead of driver 3 in virtually every simulation.
    """
    no_sc_model = MagicMock()
    no_sc_model.probability_within.return_value = 0.0

    result = simulate_race(race_state, tire_deg_pipelines, pit_model, no_sc_model, rng_seed=17)
    driver0_id = race_state.drivers[0].driver_id
    driver3_id = race_state.drivers[3].driver_id
    dist0 = next(d for d in result.driver_distributions if d.driver_id == driver0_id)
    dist3 = next(d for d in result.driver_distributions if d.driver_id == driver3_id)

    assert dist0.finish_ahead_probability[driver3_id] > 0.99
    assert dist3.finish_ahead_probability[driver0_id] < 0.01
    assert dist0.finish_ahead_probability[driver3_id] + dist3.finish_ahead_probability[
        driver0_id
    ] == pytest.approx(1.0, abs=1e-9)

    # One entry per OTHER driver in the field, never a self-entry.
    assert set(dist0.finish_ahead_probability) == {
        d.driver_id for d in race_state.drivers if d.driver_id != driver0_id
    }
