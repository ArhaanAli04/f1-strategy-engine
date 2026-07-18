"""Unit tests for services/ml/safety_car_model.py.

SafetyCarModel is a frozen dataclass — constructed directly with fitted-looking
circuit_rates/default_rate, no training data needed to exercise its math.
"""

import numpy as np
import pandas as pd
import pytest

from backend.services.ml.safety_car_model import (
    MIN_LAPS_FOR_CIRCUIT_ESTIMATE,
    SafetyCarModel,
    build_lap_flags,
    train_safety_car_model,
)

CIRCUIT_NAME = "Test Circuit"


@pytest.fixture
def model() -> SafetyCarModel:
    return SafetyCarModel(circuit_rates={CIRCUIT_NAME: 0.002}, default_rate=0.001)


@pytest.mark.unit
def test_probability_increases_under_wet_conditions(model: SafetyCarModel) -> None:
    dry_probability = model.probability_within(
        CIRCUIT_NAME, lap_number=10, wet_track=False, n_laps=5
    )
    wet_probability = model.probability_within(
        CIRCUIT_NAME, lap_number=10, wet_track=True, n_laps=5
    )
    assert wet_probability > dry_probability


@pytest.mark.unit
def test_lap_1_probability_greater_than_lap_30(model: SafetyCarModel) -> None:
    lap1_probability = model.probability_within(
        CIRCUIT_NAME, lap_number=1, wet_track=False, n_laps=1
    )
    lap30_probability = model.probability_within(
        CIRCUIT_NAME, lap_number=30, wet_track=False, n_laps=1
    )
    assert lap1_probability > lap30_probability


@pytest.mark.unit
def test_probability_within_bounded_and_grows_with_horizon(model: SafetyCarModel) -> None:
    short_horizon = model.probability_within(CIRCUIT_NAME, lap_number=10, wet_track=False, n_laps=1)
    long_horizon = model.probability_within(CIRCUIT_NAME, lap_number=10, wet_track=False, n_laps=20)
    assert 0.0 <= short_horizon <= long_horizon <= 1.0


@pytest.mark.unit
def test_unknown_circuit_falls_back_to_default_rate(model: SafetyCarModel) -> None:
    known_rate = model.rate(CIRCUIT_NAME, lap_number=10, wet_track=False)
    unknown_rate = model.rate("Unknown Circuit", lap_number=10, wet_track=False)
    assert unknown_rate == pytest.approx(model.default_rate)
    assert known_rate != unknown_rate


@pytest.mark.unit
def test_train_safety_car_model_fits_circuit_rate_from_lap_flags() -> None:
    rng = np.random.default_rng(8)
    n_laps = MIN_LAPS_FOR_CIRCUIT_ESTIMATE + 50
    sc_onset_lap = 100
    laps = pd.DataFrame(
        {
            "session_id": ["s1"] * n_laps,
            "lap_number": np.arange(1, n_laps + 1),
            "track_status": ["4" if lap == sc_onset_lap else "1" for lap in range(1, n_laps + 1)],
            "compound": rng.choice(["MEDIUM", "HARD"], n_laps),
            "circuit_name": [CIRCUIT_NAME] * n_laps,
        }
    )

    flagged = build_lap_flags(laps)
    model = train_safety_car_model(flagged)

    assert CIRCUIT_NAME in model.circuit_rates
    assert model.circuit_rates[CIRCUIT_NAME] > 0
    assert model.default_rate >= 0.0
