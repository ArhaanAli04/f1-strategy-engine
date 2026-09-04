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
    PIT_LABEL_HORIZON_LAPS,
    TARGET_COLUMN,
    _build_model,
    add_gap_features,
    label_pit_laps,
    prepare_pit_predictor_features,
    train_pit_predictor,
)


def _synthetic_imbalanced_df(
    rng: np.random.Generator, n_sessions: int = 6, laps_per_session: int = 60
) -> pd.DataFrame:
    """Multi-session synthetic laps frame where pit_within_k_laps is ~5% positive,
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
                    TARGET_COLUMN: current_tyre_age > 38,
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

    assert result.positive_rate == pytest.approx(df[TARGET_COLUMN].mean())
    assert 0 < result.positive_rate < 0.2

    predicted_probabilities = cast(
        np.ndarray, result.model.predict_proba(df[FEATURE_COLUMNS].to_numpy(dtype=float))
    )[:, 1]
    assert predicted_probabilities.std() > 0


@pytest.mark.unit
def test_label_pit_laps_marks_horizon_before_pit_lap_not_just_out_lap() -> None:
    """The Checkpoint 6 fix itself: a stint starting at lap 8 means the driver
    actually pitted on lap 7 (start_lap - 1, the last lap on the OLD tyre —
    see label_pit_laps' own docstring). With PIT_LABEL_HORIZON_LAPS=3, laps
    5-7 (inclusive) should be positive — not just lap 7, and NOT lap 8 (the
    out-lap the old, pre-fix label marked instead, per the original finding
    this checkpoint closes)."""
    laps = pd.DataFrame(
        {
            "session_id": ["s1"] * 10,
            "driver_id": ["d1"] * 10,
            "lap_number": list(range(1, 11)),
        }
    )
    stints = pd.DataFrame(
        {
            "session_id": ["s1", "s1"],
            "driver_id": ["d1", "d1"],
            "stint_number": [1, 2],
            "start_lap": [1, 8],
        }
    )

    result = label_pit_laps(laps, stints)

    assert result.set_index("lap_number")[TARGET_COLUMN].to_dict() == {
        1: False,
        2: False,
        3: False,
        4: False,
        5: True,
        6: True,
        7: True,
        8: False,  # the out-lap — no longer marked positive, per the fix
        9: False,
        10: False,
    }


@pytest.mark.unit
def test_label_pit_laps_clips_horizon_at_lap_one() -> None:
    """A pit lap early enough that PIT_LABEL_HORIZON_LAPS laps back would go
    below lap 1 must not generate (or crash on) a nonexistent lap_number —
    the window just clips to whatever laps actually exist."""
    laps = pd.DataFrame(
        {
            "session_id": ["s1"] * 4,
            "driver_id": ["d1"] * 4,
            "lap_number": [1, 2, 3, 4],
        }
    )
    stints = pd.DataFrame(
        {
            "session_id": ["s1", "s1"],
            "driver_id": ["d1", "d1"],
            "stint_number": [1, 2],
            "start_lap": [1, 3],
        }
    )

    result = label_pit_laps(laps, stints)

    # pit_lap = 3 - 1 = 2; window = [max(1, 2-2), 2] = [1, 2].
    assert result.set_index("lap_number")[TARGET_COLUMN].to_dict() == {
        1: True,
        2: True,
        3: False,
        4: False,
    }


@pytest.mark.unit
def test_label_pit_laps_window_width_matches_horizon_constant() -> None:
    """Symbolic against PIT_LABEL_HORIZON_LAPS itself (not a hardcoded 3),
    so a future retune of the constant doesn't silently invalidate this
    test's own assumption about the window width."""
    n_laps = 50
    pit_lap = 30
    laps = pd.DataFrame(
        {
            "session_id": ["s1"] * n_laps,
            "driver_id": ["d1"] * n_laps,
            "lap_number": list(range(1, n_laps + 1)),
        }
    )
    stints = pd.DataFrame(
        {
            "session_id": ["s1", "s1"],
            "driver_id": ["d1", "d1"],
            "stint_number": [1, 2],
            "start_lap": [1, pit_lap + 1],
        }
    )

    result = label_pit_laps(laps, stints)

    positive_laps = sorted(result.loc[result[TARGET_COLUMN], "lap_number"])
    assert len(positive_laps) == PIT_LABEL_HORIZON_LAPS
    assert positive_laps == list(range(pit_lap - PIT_LABEL_HORIZON_LAPS + 1, pit_lap + 1))


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

    # pit_lap = 4 - 1 = 3 (the last lap on the old tyre); the out-lap itself
    # (lap 4) is no longer positive — see label_pit_laps' own tests for the
    # full window-boundary behavior this delegates to.
    assert bool(result.loc[result["lap_number"] == 3, TARGET_COLUMN].iloc[0])
    assert not bool(result.loc[result["lap_number"] == 4, TARGET_COLUMN].iloc[0])
    assert (result["current_tyre_age"] == result["tyre_age_laps"]).all()
    assert (result["laps_to_race_end"] == result["laps_in_session"] - result["lap_number"]).all()
    assert (result["fuel_load_est"] >= 0).all()
