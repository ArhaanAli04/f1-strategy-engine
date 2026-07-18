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

from backend.services.ml.pit_predictor import FEATURE_COLUMNS as PIT_FEATURE_COLUMNS
from backend.services.ml.pit_predictor import _build_model
from backend.services.ml.race_simulator import (
    DriverRaceState,
    RaceSimulationInput,
    RaceSimulationResult,
    simulate_race,
)
from backend.services.ml.safety_car_model import SafetyCarModel
from backend.services.ml.tire_deg_model import FEATURE_COLUMNS as TIRE_FEATURE_COLUMNS
from backend.services.ml.tire_deg_model import _build_pipeline

CIRCUIT_NAME = "Test Circuit"
COMPOUND = "MEDIUM"
COMPOUND_ENCODED = 2
N_DRIVERS = 4


def _synthetic_tire_pipeline(seed: int) -> Any:
    rng = np.random.default_rng(seed)
    n = 60
    features = rng.random((n, len(TIRE_FEATURE_COLUMNS)))
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
