"""Unit tests for services/ml/tire_deg_model.py — synthetic pipeline only, no real .pkl files.

trained_tire_model (conftest.py) fits a real StandardScaler->XGBRegressor pipeline on
random data with the correct FEATURE_COLUMNS shape. These tests exercise pipeline
mechanics (the .predict() contract, shape validation), not real tyre-degradation
behavior — that's covered by integration tests against the actual promoted models.
"""

import zlib
from typing import Any

import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from backend.services.ml.tire_deg_model import (
    ASSUMED_START_FUEL_KG,
    FEATURE_COLUMNS,
    FUEL_TIME_PENALTY_PER_KG,
    MAX_LOOKAHEAD_LAPS,
    CategoricalEncodingMaps,
    _build_pipeline,
    add_engineered_features,
    apply_incompatible_model_fallbacks,
    build_categorical_encoding_maps,
    encoding_maps_from_metrics,
    fuel_load_penalty_seconds,
    pipeline_feature_count,
    predict_life_remaining_batch,
    project_stint_delta,
    resolve_circuit_code,
    resolve_driver_code,
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
                    "fuel_load_penalty": float(rng.random()),
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

    assert "fuel_load_penalty" in result.columns
    assert "fuel_corrected_time" in result.columns
    assert not result["track_temp"].isna().any()
    assert not result["air_temp"].isna().any()

    # The target is a deviation from the median of the FUEL-CORRECTED times,
    # not of the raw ones — correcting both sides with the same term is what
    # stops the median subtraction reintroducing the fuel trend.
    penalty = fuel_load_penalty_seconds(df["lap_number"], df["laps_in_session"])
    corrected = df["lap_time_seconds"] - penalty
    assert result["fuel_corrected_time"].tolist() == pytest.approx(corrected.tolist())
    assert result.loc[0, "lap_time_delta"] == pytest.approx(corrected.iloc[0] - corrected.median())


@pytest.mark.unit
def test_fuel_load_penalty_decreases_as_the_race_runs() -> None:
    """The car gets lighter, so the penalty it carries must SHRINK toward zero.

    Regression test for the 2026-09-09 inverted-sign defect (see the module
    docstring): the previous formula subtracted fuel BURNED, which grows across
    a race, so it made late laps look faster still instead of normalising them.
    """
    penalty = fuel_load_penalty_seconds(np.array([1.0, 25.0, 50.0]), 50.0)

    assert penalty[0] > penalty[1] > penalty[2]
    assert penalty[2] == pytest.approx(0.0)
    # Lap 1 carries very nearly the full tank's penalty.
    assert penalty[0] == pytest.approx(
        FUEL_TIME_PENALTY_PER_KG * ASSUMED_START_FUEL_KG * (1 - 1 / 50), rel=1e-9
    )


@pytest.mark.unit
def test_fuel_load_penalty_is_never_negative_and_survives_degenerate_inputs() -> None:
    """total_laps <= 0 and laps past the flag must not produce a negative penalty."""
    assert fuel_load_penalty_seconds(5.0, 0.0) >= 0.0
    assert fuel_load_penalty_seconds(80.0, 50.0) == pytest.approx(0.0)
    assert np.all(fuel_load_penalty_seconds(np.array([0.0, 10.0, 999.0]), 50.0) >= 0.0)


@pytest.mark.unit
def test_engineered_target_recovers_a_positive_degradation_slope() -> None:
    """A synthetic stint that genuinely degrades must produce a POSITIVE target slope.

    The core regression test for both 2026-09-09 defects at once. Lap times here
    are built as (constant base) + (real degradation) - (real fuel gain), i.e.
    exactly what a real degrading car does. Under the old fuel handling the fuel
    term dominated and the resulting target sloped DOWNWARD with tyre age,
    teaching every model that tyres improve as they age.
    """
    total_laps = 50
    laps = np.arange(1, total_laps + 1)
    true_degradation_per_lap = 0.06
    real_fuel_effect = FUEL_TIME_PENALTY_PER_KG * ASSUMED_START_FUEL_KG * (1 - laps / total_laps)
    df = pd.DataFrame(
        {
            "session_id": ["s1"] * total_laps,
            "driver_id": ["d1"] * total_laps,
            "lap_number": laps,
            "lap_time_seconds": 90.0 + true_degradation_per_lap * laps + real_fuel_effect,
            "laps_in_session": [total_laps] * total_laps,
            "compound": ["MEDIUM"] * total_laps,
            "circuit_id_encoded": [1] * total_laps,
            "track_temp": [35.0] * total_laps,
            "air_temp": [25.0] * total_laps,
        }
    )

    result = add_engineered_features(df)
    slope = float(np.polyfit(result["lap_number"], result["lap_time_delta"], 1)[0])

    assert slope == pytest.approx(true_degradation_per_lap, abs=1e-9)


@pytest.mark.unit
def test_fuel_load_penalty_feature_never_encodes_a_lap_time() -> None:
    """The feature must be identical for two frames that differ ONLY in lap time.

    Guards the leakage half of the 2026-09-09 fix: the old fuel_adjusted_time
    was `lap_time_seconds - penalty`, so it carried the target into the feature
    matrix and could not be reconstructed by a forward simulation that has no
    lap times. fuel_load_penalty depends on race position alone, which is what
    makes training and inference produce the same value by construction.
    """
    base = {
        "session_id": ["s1", "s1", "s1"],
        "driver_id": ["d1", "d1", "d1"],
        "lap_number": [1, 2, 3],
        "laps_in_session": [3, 3, 3],
        "compound": ["MEDIUM"] * 3,
        "circuit_id_encoded": [1, 1, 1],
        "track_temp": [35.0] * 3,
        "air_temp": [25.0] * 3,
    }
    slow = add_engineered_features(pd.DataFrame({**base, "lap_time_seconds": [90.0, 91.0, 92.0]}))
    fast = add_engineered_features(pd.DataFrame({**base, "lap_time_seconds": [70.0, 71.0, 72.0]}))

    assert slow["fuel_load_penalty"].tolist() == pytest.approx(fast["fuel_load_penalty"].tolist())


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
        fuel_load_penalty=np.array([0.0, 0.0]),
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
        fuel_load_penalty=np.array([0.0]),
        circuit_id_encoded=np.array([0], dtype=np.int64),
        driver_id_encoded=np.array([0], dtype=np.int64),
    )
    assert result[0] == MAX_LOOKAHEAD_LAPS


# --- pipeline_feature_count / apply_incompatible_model_fallbacks ---
# Covers the WET/INTER schema-mismatch alias documented in
# docs/simulator-issues-wet-model-and-position-context.md.


def _fit_pipeline_with_n_features(n_features: int, seed: int) -> Pipeline:
    rng = np.random.default_rng(seed)
    n_samples = 30
    features = rng.random((n_samples, n_features))
    target = rng.normal(0.0, 0.3, n_samples)
    pipeline = _build_pipeline()
    pipeline.fit(features, target)
    return pipeline


@pytest.mark.unit
def test_pipeline_feature_count_reads_fitted_scaler() -> None:
    pipeline = _fit_pipeline_with_n_features(len(FEATURE_COLUMNS), seed=10)
    assert pipeline_feature_count(pipeline) == len(FEATURE_COLUMNS)


@pytest.mark.unit
def test_pipeline_feature_count_none_for_unfitted_pipeline() -> None:
    assert pipeline_feature_count(_build_pipeline()) is None


@pytest.mark.unit
def test_pipeline_feature_count_none_for_non_pipeline_object() -> None:
    assert pipeline_feature_count(object()) is None
    assert pipeline_feature_count(None) is None


@pytest.mark.unit
def test_apply_incompatible_model_fallbacks_aliases_mismatched_wet() -> None:
    stale_wet = _fit_pipeline_with_n_features(8, seed=11)
    inter = _fit_pipeline_with_n_features(len(FEATURE_COLUMNS), seed=12)
    models = {"tire_deg_wet.pkl": stale_wet, "tire_deg_inter.pkl": inter}

    apply_incompatible_model_fallbacks(models)

    assert models["tire_deg_wet.pkl"] is inter


@pytest.mark.unit
def test_apply_incompatible_model_fallbacks_leaves_compatible_wet_untouched() -> None:
    good_wet = _fit_pipeline_with_n_features(len(FEATURE_COLUMNS), seed=13)
    inter = _fit_pipeline_with_n_features(len(FEATURE_COLUMNS), seed=14)
    models = {"tire_deg_wet.pkl": good_wet, "tire_deg_inter.pkl": inter}

    apply_incompatible_model_fallbacks(models)

    assert models["tire_deg_wet.pkl"] is good_wet


@pytest.mark.unit
def test_apply_incompatible_model_fallbacks_noop_when_fallback_missing() -> None:
    stale_wet = _fit_pipeline_with_n_features(8, seed=15)
    models = {"tire_deg_wet.pkl": stale_wet}

    apply_incompatible_model_fallbacks(models)

    assert models["tire_deg_wet.pkl"] is stale_wet


@pytest.mark.unit
def test_apply_incompatible_model_fallbacks_noop_when_model_absent() -> None:
    models: dict[str, Any] = {"tire_deg_inter.pkl": _fit_pipeline_with_n_features(6, seed=16)}
    apply_incompatible_model_fallbacks(models)  # must not raise / must not add tire_deg_wet.pkl
    assert "tire_deg_wet.pkl" not in models


@pytest.mark.unit
def test_apply_incompatible_model_fallbacks_aliases_parallel_cache() -> None:
    """WET/INTER model alias also aliases a parallel filename-keyed cache (e.g. encoding maps)."""
    stale_wet = _fit_pipeline_with_n_features(8, seed=17)
    inter = _fit_pipeline_with_n_features(len(FEATURE_COLUMNS), seed=18)
    models = {"tire_deg_wet.pkl": stale_wet, "tire_deg_inter.pkl": inter}
    inter_maps = CategoricalEncodingMaps(
        driver_id_to_code={"d1": 0}, circuit_name_to_code={"c1": 0}
    )
    maps_cache: dict[str, Any] = {
        "tire_deg_wet.pkl": "stale-own-maps",
        "tire_deg_inter.pkl": inter_maps,
    }

    apply_incompatible_model_fallbacks(models, maps_cache)

    assert maps_cache["tire_deg_wet.pkl"] is inter_maps


@pytest.mark.unit
def test_apply_incompatible_model_fallbacks_leaves_parallel_cache_when_fallback_absent() -> None:
    """A parallel cache with no entry for the fallback filename is left untouched for it."""
    stale_wet = _fit_pipeline_with_n_features(8, seed=19)
    inter = _fit_pipeline_with_n_features(len(FEATURE_COLUMNS), seed=20)
    models = {"tire_deg_wet.pkl": stale_wet, "tire_deg_inter.pkl": inter}
    maps_cache: dict[str, Any] = {"tire_deg_wet.pkl": "stale-own-maps"}

    apply_incompatible_model_fallbacks(models, maps_cache)

    assert maps_cache == {"tire_deg_wet.pkl": "stale-own-maps"}


# --- build_categorical_encoding_maps / encoding_maps_from_metrics /
# --- resolve_driver_code / resolve_circuit_code ---
# Covers the training-vs-inference encoding mismatch quantified by
# scripts/evaluate_driver_features.py (see CLAUDE.md's Deferred Wiring entry).


@pytest.mark.unit
def test_build_categorical_encoding_maps_recovers_unique_codes() -> None:
    df = pd.DataFrame(
        {
            "driver_id": ["d1", "d1", "d2", "d3"],
            "driver_id_encoded": [0, 0, 1, 2],
            "circuit_name": ["Silverstone", "Silverstone", "Monza", "Monza"],
            "circuit_id_encoded": [5, 5, 9, 9],
        }
    )

    maps = build_categorical_encoding_maps(df)

    assert maps == {
        "driver_id_to_code": {"d1": 0, "d2": 1, "d3": 2},
        "circuit_name_to_code": {"Silverstone": 5, "Monza": 9},
    }


@pytest.mark.unit
def test_build_categorical_encoding_maps_values_are_plain_python_ints() -> None:
    """numpy scalar codes must not leak into the map — json.dumps would reject them."""
    df = pd.DataFrame(
        {
            "driver_id": ["d1"],
            "driver_id_encoded": pd.array([0], dtype="int8"),
            "circuit_name": ["Silverstone"],
            "circuit_id_encoded": pd.array([5], dtype="int8"),
        }
    )

    maps = build_categorical_encoding_maps(df)

    assert type(maps["driver_id_to_code"]["d1"]) is int
    assert type(maps["circuit_name_to_code"]["Silverstone"]) is int


@pytest.mark.unit
def test_encoding_maps_from_metrics_recovers_dataclass() -> None:
    metrics = {
        "holdout_mae": 0.5,
        "driver_id_to_code": {"d1": 0},
        "circuit_name_to_code": {"c1": 0},
    }

    maps = encoding_maps_from_metrics(metrics)

    assert maps == CategoricalEncodingMaps(
        driver_id_to_code={"d1": 0}, circuit_name_to_code={"c1": 0}
    )


@pytest.mark.unit
def test_encoding_maps_from_metrics_none_when_metrics_none() -> None:
    assert encoding_maps_from_metrics(None) is None


@pytest.mark.unit
def test_encoding_maps_from_metrics_none_for_legacy_sidecar_missing_maps() -> None:
    """A pre-fix sidecar (or a non-tire_deg model's sidecar) has no encoding-map keys at all."""
    assert encoding_maps_from_metrics({"holdout_mae": 0.5}) is None


@pytest.mark.unit
def test_resolve_driver_code_uses_real_map_when_present() -> None:
    maps = CategoricalEncodingMaps(driver_id_to_code={"d1": 7}, circuit_name_to_code={})
    assert resolve_driver_code(maps, "d1") == 7


@pytest.mark.unit
def test_resolve_driver_code_falls_back_when_maps_none() -> None:
    """Identical to the pre-fix crc32 formula, so behavior only ever improves, never regresses."""
    assert resolve_driver_code(None, "unknown-driver") == zlib.crc32(b"unknown-driver") % 1000


@pytest.mark.unit
def test_resolve_driver_code_falls_back_when_driver_missing_from_map() -> None:
    maps = CategoricalEncodingMaps(driver_id_to_code={"d1": 7}, circuit_name_to_code={})
    assert resolve_driver_code(maps, "d2") == zlib.crc32(b"d2") % 1000


@pytest.mark.unit
def test_resolve_circuit_code_uses_real_map_when_present() -> None:
    maps = CategoricalEncodingMaps(driver_id_to_code={}, circuit_name_to_code={"Monza": 9})
    assert resolve_circuit_code(maps, "Monza") == 9


@pytest.mark.unit
def test_resolve_circuit_code_falls_back_when_circuit_missing_from_map() -> None:
    maps = CategoricalEncodingMaps(driver_id_to_code={}, circuit_name_to_code={"Monza": 9})
    expected = zlib.crc32(b"Unknown Circuit") % 1000
    assert resolve_circuit_code(maps, "Unknown Circuit") == expected


# --- project_stint_delta ---
# The shared implementation behind both strategy_service._project_stint_delta
# (undercut/overcut projection) and prediction_worker._build_plan_explanation
# (What-If Simulator rebuild part (a) — see
# docs/core-feature-rebuild-whatif-simulator.md §7). These tests exercise the
# function directly rather than through either caller.


def _naive_stint_delta(
    pipeline: Pipeline,
    compound_encoded: int,
    driver_code: int,
    circuit_code: int,
    start_lap: int,
    n_laps: int,
    start_tyre_age: int,
    total_laps: int,
) -> float:
    """Hand-built reference sum, independent of project_stint_delta's own code path.

    Deliberately re-derives the fuel term inline rather than calling
    fuel_load_penalty_seconds, so this stays an independent check of what
    project_stint_delta builds rather than a tautology against the shared helper.
    """
    laps = np.arange(start_lap, start_lap + n_laps, dtype=np.float64)
    tyre_age = start_tyre_age + np.arange(n_laps, dtype=np.float64)
    fuel_at_lap = ASSUMED_START_FUEL_KG * (1 - laps / max(total_laps, 1))
    fuel_load_penalty = FUEL_TIME_PENALTY_PER_KG * fuel_at_lap
    features = np.column_stack(
        [
            laps,
            np.full(n_laps, float(compound_encoded)),
            tyre_age,
            fuel_load_penalty,
            np.full(n_laps, float(circuit_code)),
            np.full(n_laps, float(driver_code)),
        ]
    )
    return float(pipeline.predict(features).sum())


@pytest.mark.unit
def test_project_stint_delta_matches_naive_reference() -> None:
    pipeline = _fit_pipeline_with_slope(slope=0.4, seed=30)
    kwargs: dict[str, int] = {"start_lap": 20, "n_laps": 10, "start_tyre_age": 2, "total_laps": 50}
    result = project_stint_delta(pipeline, 1, 5, 3, **kwargs)
    expected = _naive_stint_delta(pipeline, 1, 5, 3, **kwargs)
    assert result == pytest.approx(expected)


@pytest.mark.unit
def test_project_stint_delta_zero_for_zero_laps() -> None:
    pipeline = _fit_pipeline_with_slope(slope=0.4, seed=31)
    result = project_stint_delta(
        pipeline, 1, 5, 3, start_lap=20, n_laps=0, start_tyre_age=2, total_laps=50
    )
    assert result == 0.0


@pytest.mark.unit
def test_project_stint_delta_zero_for_negative_laps() -> None:
    pipeline = _fit_pipeline_with_slope(slope=0.4, seed=32)
    result = project_stint_delta(
        pipeline, 1, 5, 3, start_lap=20, n_laps=-3, start_tyre_age=2, total_laps=50
    )
    assert result == 0.0


@pytest.mark.unit
def test_project_stint_delta_none_for_none_pipeline() -> None:
    result = project_stint_delta(
        None, 1, 5, 3, start_lap=20, n_laps=10, start_tyre_age=2, total_laps=50
    )
    assert result is None


@pytest.mark.unit
def test_project_stint_delta_none_for_mismatched_pipeline() -> None:
    """Same schema-drift guard as race_simulator._tire_deg_predictions — a pipeline
    fitted on a different feature count (e.g. the stale 8-feature WET model) must
    degrade to None, not raise or silently predict on a misaligned feature vector.
    """
    mismatched = _fit_pipeline_with_n_features(8, seed=33)
    result = project_stint_delta(
        mismatched, 1, 5, 3, start_lap=20, n_laps=10, start_tyre_age=2, total_laps=50
    )
    assert result is None


@pytest.mark.unit
def test_project_stint_delta_none_when_predict_raises() -> None:
    class _RaisingPipeline:
        named_steps: dict[str, Any] = {}

        def predict(self, features: Any) -> Any:
            raise RuntimeError("boom")

    result = project_stint_delta(
        _RaisingPipeline(), 1, 5, 3, start_lap=20, n_laps=10, start_tyre_age=2, total_laps=50
    )
    assert result is None
