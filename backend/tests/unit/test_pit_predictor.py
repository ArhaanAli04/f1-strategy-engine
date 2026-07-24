"""Unit tests for services/ml/pit_predictor.py — synthetic data only, no real .pkl files.

Fits bare LGBMClassifier objects (via _build_model/train_pit_predictor) on small
synthetic frames engineered with a clear signal, so these tests validate training
mechanics (threshold crossing, probability shape, imbalance handling), not real
pit-strategy behavior — that's covered by integration tests against promoted models.
"""

from typing import cast

import numpy as np
import pandas as pd
import pytest

from backend.services.ml.pit_predictor import (
    ALERT_THRESHOLD,
    FEATURE_COLUMNS,
    MAX_GAP_SECONDS,
    _build_model,
    add_gap_features,
    label_pit_laps,
    prepare_pit_predictor_features,
    train_pit_predictor,
)


def _synthetic_imbalanced_df(
    rng: np.random.Generator, n_sessions: int = 6, laps_per_session: int = 60
) -> pd.DataFrame:
    """Multi-session synthetic laps frame where did_pit_this_lap is ~5% positive,
    driven by current_tyre_age crossing a threshold — enough sessions for GroupKFold
    (CV_FOLDS=5) and enough imbalance to exercise scale_pos_weight compensation.
    """
    rows = []
    for session_idx in range(n_sessions):
        for _ in range(laps_per_session):
            current_tyre_age = rng.uniform(0, 40)
            rows.append(
                {
                    "session_id": f"session-{session_idx}",
                    "current_tyre_age": current_tyre_age,
                    "predicted_life_remaining": rng.uniform(0, 40),
                    "gap_to_car_ahead": rng.uniform(0, 120),
                    "gap_to_car_behind": rng.uniform(0, 120),
                    "safety_car_probability": rng.uniform(0, 1),
                    "laps_to_race_end": rng.integers(0, 60),
                    "position": rng.integers(1, 21),
                    "fuel_load_est": rng.uniform(0, 110),
                    "did_pit_this_lap": current_tyre_age > 38,
                }
            )
    return pd.DataFrame(rows)


@pytest.mark.unit
def test_threshold_behaviour() -> None:
    rng = np.random.default_rng(10)
    n = 500
    features_df = pd.DataFrame({col: rng.uniform(0, 10, n) for col in FEATURE_COLUMNS})
    current_tyre_age = rng.uniform(0, 40, n)
    features_df["current_tyre_age"] = current_tyre_age
    target = (current_tyre_age > 38).astype(int)

    model = _build_model(scale_pos_weight=(1 - target.mean()) / target.mean())
    model.fit(features_df[FEATURE_COLUMNS].to_numpy(dtype=float), target)

    low_tyre_age_row = features_df.iloc[[0]].copy()
    low_tyre_age_row["current_tyre_age"] = 2.0
    high_tyre_age_row = features_df.iloc[[0]].copy()
    high_tyre_age_row["current_tyre_age"] = 39.5

    low_prob = model.predict_proba(low_tyre_age_row[FEATURE_COLUMNS].to_numpy(dtype=float))[0][1]
    high_prob = model.predict_proba(high_tyre_age_row[FEATURE_COLUMNS].to_numpy(dtype=float))[0][1]

    assert low_prob < ALERT_THRESHOLD
    assert high_prob > low_prob


@pytest.mark.unit
def test_class_probabilities_valid() -> None:
    rng = np.random.default_rng(11)
    n = 300
    features = rng.random((n, len(FEATURE_COLUMNS)))
    target = rng.integers(0, 2, n)

    model = _build_model(scale_pos_weight=1.0)
    model.fit(features, target)
    probabilities = cast(np.ndarray, model.predict_proba(features))

    assert probabilities.shape == (n, 2)
    assert np.allclose(probabilities.sum(axis=1), 1.0)
    assert np.all(probabilities >= 0)
    assert np.all(probabilities <= 1)


@pytest.mark.unit
def test_imbalanced_class_handling() -> None:
    rng = np.random.default_rng(12)
    df = _synthetic_imbalanced_df(rng)
    result = train_pit_predictor(df)

    assert result.positive_rate == pytest.approx(df["did_pit_this_lap"].mean())
    assert 0 < result.positive_rate < 0.2

    predicted_probabilities = cast(
        np.ndarray, result.model.predict_proba(df[FEATURE_COLUMNS].to_numpy(dtype=float))
    )[:, 1]
    assert predicted_probabilities.std() > 0


@pytest.mark.unit
def test_label_pit_laps_marks_stint_starts_after_first() -> None:
    laps = pd.DataFrame(
        {
            "session_id": ["s1"] * 5,
            "driver_id": ["d1"] * 5,
            "lap_number": [1, 2, 3, 4, 5],
        }
    )
    stints = pd.DataFrame(
        {
            "session_id": ["s1", "s1"],
            "driver_id": ["d1", "d1"],
            "stint_number": [1, 2],
            "start_lap": [1, 4],
        }
    )

    result = label_pit_laps(laps, stints)

    assert result.set_index("lap_number")["did_pit_this_lap"].to_dict() == {
        1: False,
        2: False,
        3: False,
        4: True,
        5: False,
    }


@pytest.mark.unit
def test_add_gap_features_computes_gaps_and_caps_leader_and_last() -> None:
    laps = pd.DataFrame(
        {
            "session_id": ["s1"] * 6,
            "driver_id": ["d1", "d1", "d1", "d2", "d2", "d2"],
            "lap_number": [1, 2, 3, 1, 2, 3],
            "lap_time_seconds": [90.0, 90.0, 90.0, 91.0, 91.0, 91.0],
            "position": [1, 1, 1, 2, 2, 2],
        }
    )

    result = add_gap_features(laps)

    lap2 = result[result["lap_number"] == 2]
    leader = lap2[lap2["position"] == 1].iloc[0]
    follower = lap2[lap2["position"] == 2].iloc[0]

    assert leader["gap_to_car_ahead"] == pytest.approx(
        MAX_GAP_SECONDS
    )  # no car ahead of the leader
    assert follower["gap_to_car_behind"] == pytest.approx(
        MAX_GAP_SECONDS
    )  # no car behind last place
    assert follower["gap_to_car_ahead"] == pytest.approx(2.0)  # cumulative_time diff: 182 - 180


@pytest.mark.unit
def test_prepare_pit_predictor_features_adds_all_derived_columns() -> None:
    laps = pd.DataFrame(
        {
            "session_id": ["s1"] * 4,
            "driver_id": ["d1"] * 4,
            "lap_number": [1, 2, 3, 4],
            "lap_time_seconds": [90.0, 90.0, 90.0, 90.0],
            "position": [1, 1, 1, 1],
            "tyre_age_laps": [0, 1, 2, 0],
            "laps_in_session": [4, 4, 4, 4],
        }
    )
    stints = pd.DataFrame(
        {
            "session_id": ["s1", "s1"],
            "driver_id": ["d1", "d1"],
            "stint_number": [1, 2],
            "start_lap": [1, 4],
        }
    )

    result = prepare_pit_predictor_features(laps, stints)

    assert bool(result.loc[result["lap_number"] == 4, "did_pit_this_lap"].iloc[0])
    assert (result["current_tyre_age"] == result["tyre_age_laps"]).all()
    assert (result["laps_to_race_end"] == result["laps_in_session"] - result["lap_number"]).all()
    assert (result["fuel_load_est"] >= 0).all()
