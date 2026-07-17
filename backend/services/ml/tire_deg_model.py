"""XGBoost tire degradation regression — one model per compound.

Predicts, for a given lap, the lap time delta from that driver's session
median lap time as a function of tyre age, fuel-adjusted pace, and
circuit/driver context. See FEATURE_COLUMNS below for why track/air
temperature are computed (weather infra) but not currently selected into
the feature set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

logger = logging.getLogger(__name__)

FEATURE_COLUMNS = [
    "lap_number",
    "compound_encoded",
    "tyre_age_laps",
    "fuel_adjusted_time",
    "circuit_id_encoded",
    "driver_id_encoded",
]
TARGET_COLUMN = "lap_time_delta"

# track_temp/air_temp were removed from FEATURE_COLUMNS on 2026-07-16: adding
# them regressed holdout MAE 30-40% (see CLAUDE.md Data Quality Notes) and the
# promotion guard correctly refused to replace production models with the
# regressed version — so the actual "production"-tagged S3 models are still
# the pre-weather 6-feature versions. This reverts the feature set to match
# what's actually deployed. _impute_weather/add_engineered_features below
# still compute imputed track_temp/air_temp columns (weather infrastructure
# stays wired per CLAUDE.md), they're just no longer selected into the
# feature matrix. Re-add both columns above once a weather-aware retrain
# improves holdout MAE and gets promoted.

# Fallback used only if a (compound, circuit) group has zero non-null weather
# readings — i.e. add_engineered_features's own group-mean imputation has
# nothing to fall back on. Should not occur post-backfill (see CLAUDE.md Data
# Quality Notes), but StandardScaler cannot tolerate a remaining NaN, so a
# last-resort constant is cheaper than letting training crash on it.
DEFAULT_TRACK_TEMP_C = 35.0
DEFAULT_AIR_TEMP_C = 25.0

# F1 cars start a race with ~110kg of fuel and burn roughly linearly to ~0kg
# by the finish; FastF1 does not publish real fuel load, so this is an
# estimate used only to compute the fuel_adjusted_time feature.
ASSUMED_START_FUEL_KG = 110.0
FUEL_TIME_PENALTY_PER_KG = 0.03

CV_FOLDS = 5
DEGRADATION_THRESHOLD_SECONDS = 1.5
MAX_LOOKAHEAD_LAPS = 40


@dataclass(frozen=True)
class TireDegTrainResult:
    pipeline: Pipeline
    cv_mae: float
    cv_rmse: float
    n_samples: int


def _impute_weather(df: pd.DataFrame) -> pd.DataFrame:
    """Fill missing track_temp/air_temp with the (compound, circuit) group mean.

    Some laps have NULL track_temp/air_temp — sessions where the weather
    backfill found no weather_data at all (see
    scripts/backfill_weather_data.py). Rows are never dropped for this, since
    that would remove too much training data; instead each NaN is imputed
    from other laps in the same compound+circuit context, which is the
    closest available proxy for "what the track was like." Any group that is
    itself entirely NaN (no compound+circuit combination ever observed
    weather) falls back to a fixed constant, since StandardScaler cannot
    tolerate a NaN reaching it.

    Args:
        df: Laps frame; must include compound, circuit_id_encoded, track_temp, air_temp.
    Returns:
        Copy of df with track_temp/air_temp NaN-free.
    """
    df = df.copy()
    for col, default in (
        ("track_temp", DEFAULT_TRACK_TEMP_C),
        ("air_temp", DEFAULT_AIR_TEMP_C),
    ):
        group_mean = df.groupby(["compound", "circuit_id_encoded"])[col].transform("mean")
        df[col] = df[col].fillna(group_mean).fillna(default)
    return df


def add_engineered_features(laps: pd.DataFrame) -> pd.DataFrame:
    """Add fuel_adjusted_time, lap_time_delta, and imputed weather columns to a raw laps frame.

    Args:
        laps: One row per lap; must include session_id, driver_id, lap_number,
            lap_time_seconds, laps_in_session (max lap_number in that session),
            compound, circuit_id_encoded, track_temp, air_temp.
    Returns:
        Copy of laps with fuel_adjusted_time and lap_time_delta added, and
        track_temp/air_temp NaN-imputed (see _impute_weather).
    """
    df = laps.copy()
    fuel_at_lap = ASSUMED_START_FUEL_KG * (1 - df["lap_number"] / df["laps_in_session"])
    df["fuel_adjusted_time"] = df["lap_time_seconds"] - FUEL_TIME_PENALTY_PER_KG * (
        ASSUMED_START_FUEL_KG - fuel_at_lap
    )
    session_median = df.groupby(["session_id", "driver_id"])["lap_time_seconds"].transform("median")
    df["lap_time_delta"] = df["lap_time_seconds"] - session_median
    df = _impute_weather(df)
    return df


def _build_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "xgb",
                XGBRegressor(n_estimators=300, learning_rate=0.05, max_depth=6, random_state=42),
            ),
        ]
    )


def train_tire_degradation_model(df: pd.DataFrame, compound: str) -> TireDegTrainResult:
    """Train one XGBoost regressor for a single tyre compound.

    Args:
        df: Feature-engineered laps for this compound only (see add_engineered_features).
        compound: Compound name, used only for logging.
    Returns:
        TireDegTrainResult with the pipeline fit on all of df and cross-validated metrics.
    """
    features = df[FEATURE_COLUMNS].to_numpy(dtype=float)
    target = df[TARGET_COLUMN].to_numpy(dtype=float)
    groups = df["session_id"].to_numpy()

    gkf = GroupKFold(n_splits=min(CV_FOLDS, df["session_id"].nunique()))
    fold_mae: list[float] = []
    fold_rmse: list[float] = []

    for train_idx, test_idx in gkf.split(features, target, groups):
        fold_pipeline = _build_pipeline()
        fold_pipeline.fit(features[train_idx], target[train_idx])
        preds = fold_pipeline.predict(features[test_idx])
        fold_mae.append(float(np.mean(np.abs(preds - target[test_idx]))))
        fold_rmse.append(float(np.sqrt(np.mean((preds - target[test_idx]) ** 2))))

    cv_mae = float(np.mean(fold_mae))
    cv_rmse = float(np.mean(fold_rmse))
    logger.info(
        "tire_deg_%s: CV MAE=%.4f RMSE=%.4f (n=%d, sessions=%d)",
        compound,
        cv_mae,
        cv_rmse,
        len(df),
        df["session_id"].nunique(),
    )

    final_pipeline = _build_pipeline()
    final_pipeline.fit(features, target)

    return TireDegTrainResult(
        pipeline=final_pipeline, cv_mae=cv_mae, cv_rmse=cv_rmse, n_samples=len(df)
    )


def evaluate_holdout(pipeline: Pipeline, df: pd.DataFrame) -> float:
    """Compute MAE of a fitted pipeline against a holdout dataframe.

    Args:
        pipeline: Fitted Pipeline (StandardScaler -> XGBRegressor).
        df: Feature-engineered holdout laps (see add_engineered_features), same compound.
    Returns:
        Mean absolute error between predicted and actual lap_time_delta.
    """
    features = df[FEATURE_COLUMNS].to_numpy(dtype=float)
    target = df[TARGET_COLUMN].to_numpy(dtype=float)
    preds = pipeline.predict(features)
    return float(np.mean(np.abs(preds - target)))


def predict_life_remaining_batch(
    pipeline: Pipeline,
    lap_number: npt.NDArray[np.int64],
    compound_encoded: npt.NDArray[np.int64],
    tyre_age_laps: npt.NDArray[np.int64],
    fuel_adjusted_time: npt.NDArray[np.float64],
    circuit_id_encoded: npt.NDArray[np.int64],
    driver_id_encoded: npt.NDArray[np.int64],
) -> npt.NDArray[np.int64]:
    """Estimate laps remaining before predicted degradation crosses the threshold.

    For each input lap, simulates tyre_age_laps + 0..MAX_LOOKAHEAD_LAPS-1 (lap_number
    advancing in step) in a single batched predict() call, holding fuel_adjusted_time
    fixed at its current-lap value — pace beyond the next few laps is dominated by
    tyre wear, not the small residual fuel effect.

    Args:
        pipeline: Fitted tire degradation pipeline for the relevant compound.
        lap_number, compound_encoded, tyre_age_laps, fuel_adjusted_time,
            circuit_id_encoded, driver_id_encoded: 1D arrays, one entry per lap.
    Returns:
        1D int array, same length as inputs: estimated laps remaining until predicted
        lap_time_delta >= DEGRADATION_THRESHOLD_SECONDS, capped at MAX_LOOKAHEAD_LAPS.
    """
    n = len(lap_number)
    offsets = np.arange(MAX_LOOKAHEAD_LAPS)

    future_lap = lap_number[:, None] + offsets[None, :]
    future_age = tyre_age_laps[:, None] + offsets[None, :]

    flat_features = np.stack(
        [
            future_lap.ravel(),
            np.repeat(compound_encoded, MAX_LOOKAHEAD_LAPS),
            future_age.ravel(),
            np.repeat(fuel_adjusted_time, MAX_LOOKAHEAD_LAPS),
            np.repeat(circuit_id_encoded, MAX_LOOKAHEAD_LAPS),
            np.repeat(driver_id_encoded, MAX_LOOKAHEAD_LAPS),
        ],
        axis=1,
    ).astype(float)

    preds = pipeline.predict(flat_features).reshape(n, MAX_LOOKAHEAD_LAPS)
    crossed = preds >= DEGRADATION_THRESHOLD_SECONDS
    first_cross = np.argmax(crossed, axis=1)
    first_cross[~crossed.any(axis=1)] = MAX_LOOKAHEAD_LAPS
    result: npt.NDArray[np.int64] = first_cross.astype(np.int64)
    return result
