"""Unit tests for services/ml/tire_deg_model.py — synthetic pipeline only, no real .pkl files.

trained_tire_model (conftest.py) fits a real StandardScaler->XGBRegressor pipeline on
random data with the correct FEATURE_COLUMNS shape. These tests exercise pipeline
mechanics (the .predict() contract, shape validation), not real tyre-degradation
behavior — that's covered by integration tests against the actual promoted models.
"""

from typing import Any

import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from backend.services.ml.tire_deg_model import (
    FEATURE_COLUMNS,
    MAX_LOOKAHEAD_LAPS,
    _build_pipeline,
    add_engineered_features,
    predict_life_remaining_batch,
    train_tire_degradation_model,
)


def _fit_pipeline_with_slope(slope: float, seed: int) -> Pipeline:
    """A synthetic tire_deg pipeline where predicted delta grows ~linearly with tyre_age_laps."""
    rng = np.random.default_rng(seed)
    n_samples = 100
    tyre_age_laps_col = FEATURE_COLUMNS.index("tyre_age_laps")
    features = rng.random((n_samples, len(FEATURE_COLUMNS)))
    features[:, tyre_age_laps_col] = rng.uniform(0, 40, n_samples)
    target = slope * features[:, tyre_age_laps_col] + rng.normal(0, 0.05, n_samples)
    pipeline = _build_pipeline()
    pipeline.fit(features, target)
    return pipeline


@pytest.mark.unit
def test_predict_returns_float(trained_tire_model: Pipeline) -> None:
    features = np.random.default_rng(0).random((1, len(FEATURE_COLUMNS)))
    prediction = trained_tire_model.predict(features)[0]
    assert isinstance(float(prediction), float)


@pytest.mark.unit
def test_soft_degrades_faster_than_hard() -> None:
    """Two synthetic pipelines fit with different tyre_age slopes: the steeper one's
    predictions must grow faster as tyre_age_laps increases. Validates that the
    pipeline can learn and reproduce a slope, not any real compound physics.
    """
    rng = np.random.default_rng(1)
    n_samples = 200
    tyre_age_laps_col = FEATURE_COLUMNS.index("tyre_age_laps")

    def _fit_with_slope(slope: float) -> Pipeline:
        features = rng.random((n_samples, len(FEATURE_COLUMNS)))
        features[:, tyre_age_laps_col] = rng.uniform(0, 30, n_samples)
        target = slope * features[:, tyre_age_laps_col] + rng.normal(0, 0.05, n_samples)
        pipeline = _build_pipeline()
        pipeline.fit(features, target)
        return pipeline

    soft_pipeline = _fit_with_slope(slope=0.15)
    hard_pipeline = _fit_with_slope(slope=0.03)

    base_features = np.zeros((2, len(FEATURE_COLUMNS)))
    base_features[:, tyre_age_laps_col] = [5, 25]

    soft_predictions = soft_pipeline.predict(base_features)
    hard_predictions = hard_pipeline.predict(base_features)

    soft_growth = soft_predictions[1] - soft_predictions[0]
    hard_growth = hard_predictions[1] - hard_predictions[0]
    assert soft_growth > hard_growth


@pytest.mark.unit
def test_model_output_in_reasonable_range(trained_tire_model: Pipeline) -> None:
    features = np.random.default_rng(2).random((20, len(FEATURE_COLUMNS)))
    predictions = trained_tire_model.predict(features)
    assert np.all(predictions > -5)
    assert np.all(predictions < 10)


@pytest.mark.unit
def test_missing_features_raises_error(trained_tire_model: Pipeline) -> None:
    """No named-feature validation exists in this module — the natural failure mode
    is sklearn's own shape check when a feature column is missing from the array.
    """
    features_missing_one_column = np.random.default_rng(3).random((1, len(FEATURE_COLUMNS) - 1))
    with pytest.raises(ValueError, match="features"):
        trained_tire_model.predict(features_missing_one_column)


@pytest.mark.unit
def test_train_tire_degradation_model_returns_fitted_pipeline_and_metrics() -> None:
    rng = np.random.default_rng(7)
    n_sessions = 3
    rows_per_session = 20
    rows: list[dict[str, Any]] = []
    for session_idx in range(n_sessions):
        for lap_number in range(rows_per_session):
            rows.append(
                {
                    "session_id": f"session-{session_idx}",
                    "lap_number": lap_number + 1,
                    "compound_encoded": 2,
                    "tyre_age_laps": lap_number,
                    "fuel_adjusted_time": float(rng.random()),
                    "circuit_id_encoded": 1,
                    "driver_id_encoded": 0,
                    "lap_time_delta": 0.05 * lap_number + rng.normal(0, 0.05),
                }
            )
    df = pd.DataFrame(rows)

    result = train_tire_degradation_model(df, compound="SOFT")

    assert isinstance(result.pipeline, Pipeline)
    assert result.n_samples == len(df)
    assert result.cv_mae >= 0
    assert result.cv_rmse >= 0


@pytest.mark.unit
def test_add_engineered_features_computes_delta_and_imputes_weather() -> None:
    df = pd.DataFrame(
        {
            "session_id": ["s1", "s1", "s1"],
            "driver_id": ["d1", "d1", "d1"],
            "lap_number": [1, 2, 3],
            "lap_time_seconds": [90.0, 91.0, 92.0],
            "laps_in_session": [3, 3, 3],
            "compound": ["MEDIUM", "MEDIUM", "MEDIUM"],
            "circuit_id_encoded": [1, 1, 1],
            "track_temp": [35.0, np.nan, 35.0],
            "air_temp": [25.0, 25.0, np.nan],
        }
    )

    result = add_engineered_features(df)

    assert "fuel_adjusted_time" in result.columns
    assert not result["track_temp"].isna().any()
    assert not result["air_temp"].isna().any()
    expected_median = df["lap_time_seconds"].median()
    assert result.loc[0, "lap_time_delta"] == pytest.approx(90.0 - expected_median)


@pytest.mark.unit
def test_predict_life_remaining_batch_crosses_threshold_before_cap() -> None:
    pipeline = _fit_pipeline_with_slope(
        slope=0.5, seed=5
    )  # steep: crosses 1.5s well within 40 laps
    result = predict_life_remaining_batch(
        pipeline,
        lap_number=np.array([10, 10], dtype=np.int64),
        compound_encoded=np.array([2, 2], dtype=np.int64),
        tyre_age_laps=np.array([0, 0], dtype=np.int64),
        fuel_adjusted_time=np.array([0.0, 0.0]),
        circuit_id_encoded=np.array([0, 0], dtype=np.int64),
        driver_id_encoded=np.array([0, 0], dtype=np.int64),
    )
    assert np.all(result < MAX_LOOKAHEAD_LAPS)


@pytest.mark.unit
def test_predict_life_remaining_batch_caps_when_never_crossing() -> None:
    rng = np.random.default_rng(6)
    n_samples = 50
    features = rng.random((n_samples, len(FEATURE_COLUMNS)))
    target = np.zeros(n_samples)  # never crosses DEGRADATION_THRESHOLD_SECONDS
    pipeline = _build_pipeline()
    pipeline.fit(features, target)

    result = predict_life_remaining_batch(
        pipeline,
        lap_number=np.array([10], dtype=np.int64),
        compound_encoded=np.array([2], dtype=np.int64),
        tyre_age_laps=np.array([0], dtype=np.int64),
        fuel_adjusted_time=np.array([0.0]),
        circuit_id_encoded=np.array([0], dtype=np.int64),
        driver_id_encoded=np.array([0], dtype=np.int64),
    )
    assert result[0] == MAX_LOOKAHEAD_LAPS
