"""LightGBM pit-lap classifier — probability that a driver pits on a given lap.

did_pit_this_lap is heavily imbalanced (a driver pits ~1-3 times across a
~50-70 lap race), so scale_pos_weight is used to counter it. predicted_life_remaining
and safety_car_probability are cross-model features computed by the training
orchestrator (scripts/train_models.py) from the tire degradation and safety
car models respectively, then merged in before training.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

logger = logging.getLogger(__name__)

FEATURE_COLUMNS = [
    "current_tyre_age",
    "predicted_life_remaining",
    "gap_to_car_ahead",
    "gap_to_car_behind",
    "safety_car_probability",
    "laps_to_race_end",
    "position",
    "fuel_load_est",
]
TARGET_COLUMN = "did_pit_this_lap"
ALERT_THRESHOLD = 0.65
CV_FOLDS = 5

# Same fuel-burn assumption as tire_deg_model.py; duplicated rather than
# imported since services/ml modules must not import each other directly —
# cross-model composition happens in scripts/train_models.py.
ASSUMED_START_FUEL_KG = 110.0

# A gap larger than this (seconds) is capped — huge/undefined gaps (leader,
# last car, lapped traffic) shouldn't dominate the feature's scale.
MAX_GAP_SECONDS = 120.0


@dataclass(frozen=True)
class PitPredictorTrainResult:
    model: LGBMClassifier
    cv_auc: float
    n_samples: int
    positive_rate: float


def label_pit_laps(laps: pd.DataFrame, stints: pd.DataFrame) -> pd.DataFrame:
    """Add did_pit_this_lap: True on a stint's start_lap, for every stint after the first.

    Args:
        laps: One row per lap; must include session_id, driver_id, lap_number.
        stints: tire_stints rows; must include session_id, driver_id, stint_number, start_lap.
    Returns:
        Copy of laps with a did_pit_this_lap boolean column added.
    """
    pit_laps = stints[
        stints["stint_number"]
        > stints.groupby(["session_id", "driver_id"])["stint_number"].transform("min")
    ][["session_id", "driver_id", "start_lap"]].rename(columns={"start_lap": "lap_number"})

    df = laps.copy()
    pit_lap_keys = pd.MultiIndex.from_frame(pit_laps[["session_id", "driver_id", "lap_number"]])
    row_keys = pd.MultiIndex.from_frame(df[["session_id", "driver_id", "lap_number"]])
    df["did_pit_this_lap"] = row_keys.isin(pit_lap_keys)
    return df


def add_gap_features(laps: pd.DataFrame) -> pd.DataFrame:
    """Add gap_to_car_ahead and gap_to_car_behind, from position + cumulative lap time.

    Args:
        laps: One row per lap; must include session_id, driver_id, lap_number,
            lap_time_seconds, position. Rows with no position are dropped.
    Returns:
        Copy of laps (position not-null only) with gap_to_car_ahead/behind added,
        each capped at MAX_GAP_SECONDS and defaulting to that cap for the leader
        (no car ahead) or last car (no car behind).
    """
    df = laps.dropna(subset=["position"]).copy()
    df["position"] = df["position"].astype(int)
    df = df.sort_values(["session_id", "driver_id", "lap_number"])
    df["cumulative_time"] = df.groupby(["session_id", "driver_id"])["lap_time_seconds"].cumsum()

    by_lap = df[["session_id", "lap_number", "position", "cumulative_time"]].copy()
    ahead = by_lap.copy()
    ahead["position"] += 1
    ahead = ahead.rename(columns={"cumulative_time": "ahead_cumulative_time"})
    behind = by_lap.copy()
    behind["position"] -= 1
    behind = behind.rename(columns={"cumulative_time": "behind_cumulative_time"})

    df = df.merge(ahead, on=["session_id", "lap_number", "position"], how="left")
    df = df.merge(behind, on=["session_id", "lap_number", "position"], how="left")

    df["gap_to_car_ahead"] = (df["cumulative_time"] - df["ahead_cumulative_time"]).clip(
        lower=0, upper=MAX_GAP_SECONDS
    )
    df["gap_to_car_behind"] = (df["behind_cumulative_time"] - df["cumulative_time"]).clip(
        lower=0, upper=MAX_GAP_SECONDS
    )
    df["gap_to_car_ahead"] = df["gap_to_car_ahead"].fillna(MAX_GAP_SECONDS)
    df["gap_to_car_behind"] = df["gap_to_car_behind"].fillna(MAX_GAP_SECONDS)

    return df.drop(columns=["ahead_cumulative_time", "behind_cumulative_time", "cumulative_time"])


def prepare_pit_predictor_features(laps: pd.DataFrame, stints: pd.DataFrame) -> pd.DataFrame:
    """Build the pit_predictor training frame's laps-only features and label.

    predicted_life_remaining and safety_car_probability are NOT added here — the
    orchestrator adds them afterwards, since they require the fitted tire
    degradation and safety car models.

    Args:
        laps: Raw laps frame; must include session_id, driver_id, lap_number,
            lap_time_seconds, tyre_age_laps, position, laps_in_session.
        stints: tire_stints rows for the same laps.
    Returns:
        DataFrame with current_tyre_age, gap_to_car_ahead, gap_to_car_behind,
        laps_to_race_end, position, fuel_load_est, did_pit_this_lap, session_id.
    """
    df = add_gap_features(laps)
    df = label_pit_laps(df, stints)

    df["current_tyre_age"] = df["tyre_age_laps"]
    df["laps_to_race_end"] = df["laps_in_session"] - df["lap_number"]
    fuel_at_lap = ASSUMED_START_FUEL_KG * (1 - df["lap_number"] / df["laps_in_session"])
    df["fuel_load_est"] = fuel_at_lap.clip(lower=0)

    return df


def _build_model(scale_pos_weight: float) -> LGBMClassifier:
    return LGBMClassifier(
        n_estimators=300,
        learning_rate=0.05,
        max_depth=6,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
    )


def train_pit_predictor(df: pd.DataFrame) -> PitPredictorTrainResult:
    """Train the LightGBM pit-lap classifier.

    Args:
        df: One row per lap with FEATURE_COLUMNS + did_pit_this_lap + session_id
            (predicted_life_remaining and safety_car_probability must already be merged in).
    Returns:
        PitPredictorTrainResult with the model fit on all of df and cross-validated AUC.
    """
    features = df[FEATURE_COLUMNS].to_numpy(dtype=float)
    target = df[TARGET_COLUMN].to_numpy(dtype=int)
    groups = df["session_id"].to_numpy()

    positive_rate = float(target.mean())
    scale_pos_weight = (1 - positive_rate) / positive_rate if positive_rate > 0 else 1.0

    gkf = GroupKFold(n_splits=min(CV_FOLDS, df["session_id"].nunique()))
    fold_auc: list[float] = []
    for train_idx, test_idx in gkf.split(features, target, groups):
        fold_model = _build_model(scale_pos_weight)
        fold_model.fit(features[train_idx], target[train_idx])
        probs = fold_model.predict_proba(features[test_idx])[:, 1]
        if len(np.unique(target[test_idx])) > 1:
            fold_auc.append(float(roc_auc_score(target[test_idx], probs)))

    cv_auc = float(np.mean(fold_auc)) if fold_auc else float("nan")
    logger.info(
        "pit_predictor: CV AUC=%.4f positive_rate=%.4f (n=%d, sessions=%d)",
        cv_auc,
        positive_rate,
        len(df),
        df["session_id"].nunique(),
    )

    final_model = _build_model(scale_pos_weight)
    final_model.fit(features, target)

    return PitPredictorTrainResult(
        model=final_model, cv_auc=cv_auc, n_samples=len(df), positive_rate=positive_rate
    )


def evaluate_holdout(model: LGBMClassifier, df: pd.DataFrame) -> float:
    """MAE between predicted pit probability and the actual did_pit_this_lap indicator.

    Args:
        model: Fitted LGBMClassifier.
        df: Feature-engineered holdout laps (see prepare_pit_predictor_features,
            with predicted_life_remaining/safety_car_probability merged in).
    Returns:
        Mean absolute error, comparable across model versions for promotion decisions.
    """
    features = df[FEATURE_COLUMNS].to_numpy(dtype=float)
    target = df[TARGET_COLUMN].to_numpy(dtype=float)
    preds = model.predict_proba(features)[:, 1]
    return float(np.mean(np.abs(preds - target)))
