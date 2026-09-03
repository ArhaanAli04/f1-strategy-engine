"""XGBoost tire degradation regression — one model per compound.

Predicts, for a given lap, the lap time delta from that driver's session
median lap time as a function of tyre age, fuel-adjusted pace, and
circuit/driver context. See FEATURE_COLUMNS below for why track/air
temperature are computed (weather infra) but not currently selected into
the feature set.
"""

from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass
from typing import Any

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

# Model-registry filename -> fallback filename to substitute at load time if
# the primary model's fitted feature count doesn't match len(FEATURE_COLUMNS)
# — see apply_incompatible_model_fallbacks below. Currently one entry:
# tire_deg_wet.pkl is a stale 8-feature artifact from the reverted 2026-07-10
# weather experiment (see CLAUDE.md's simulator-issues doc); tire_deg_inter.pkl
# is the closest available proxy (both wet-weather compounds, INTER has far
# more training data and a much better holdout MAE than the stale WET model).
INCOMPATIBLE_TYRE_MODEL_FALLBACKS = {"tire_deg_wet.pkl": "tire_deg_inter.pkl"}


def pipeline_feature_count(pipeline: Any) -> int | None:
    """Fitted input feature count for a tire_deg Pipeline, if determinable.

    Args:
        pipeline: A fitted Pipeline (StandardScaler -> XGBRegressor), or any
            other object (defensive — a model registry entry could in
            principle be missing/wrong-typed).
    Returns:
        The scaler's n_features_in_ as an int, or None if the pipeline isn't
        a fitted sklearn Pipeline with that attribute (unfitted, mock, or
        unexpected object) — callers must treat None as "can't tell", not as
        a mismatch.
    """
    try:
        n_features = pipeline.named_steps["scaler"].n_features_in_
    except (AttributeError, KeyError, TypeError):
        return None
    return int(n_features) if isinstance(n_features, int | np.integer) else None


def apply_incompatible_model_fallbacks(
    models: dict[str, Any], *parallel_caches: dict[str, Any]
) -> None:
    """Replace any registry model whose feature count doesn't match FEATURE_COLUMNS.

    Guards against exactly the failure class documented in
    docs/simulator-issues-wet-model-and-position-context.md: the MAE-only
    promotion guard in scripts/train_models.py can keep a schema-incompatible
    model in production (a candidate that can't beat a stale incumbent's MAE
    is never promoted, even when the incumbent's feature schema no longer
    matches current inference code). Called once at the end of each
    _load_models() (strategy_service.py and prediction_worker.py both have
    their own copy — same no-cross-service-import convention as their other
    duplicated helpers).

    Args:
        models: The model registry cache, mutated in place — filename to
            deserialized model object.
        *parallel_caches: Any number of additional filename-keyed dicts to
            alias in lockstep with models whenever a model gets aliased to
            its fallback — e.g. an encoding-maps cache (see
            CategoricalEncodingMaps below), so a model aliased to
            tire_deg_inter.pkl also picks up tire_deg_inter.pkl's own
            encoding maps rather than keeping its own (possibly stale/
            schema-incompatible) ones. A cache missing the fallback
            filename's entry is left untouched for that filename — nothing
            to alias to.
    Returns:
        None (in-place mutation of every dict passed).
    """
    expected = len(FEATURE_COLUMNS)
    for filename, fallback_filename in INCOMPATIBLE_TYRE_MODEL_FALLBACKS.items():
        pipeline = models.get(filename)
        if pipeline is None:
            continue
        n_features = pipeline_feature_count(pipeline)
        if n_features is None or n_features == expected:
            continue
        fallback = models.get(fallback_filename)
        if fallback is None:
            logger.error(
                "%s has %d features (expected %d) and no fallback %s is loaded — "
                "leaving it in place, inference will likely crash or degrade",
                filename,
                n_features,
                expected,
                fallback_filename,
            )
            continue
        logger.warning(
            "%s has %d features (expected %d) — aliasing to %s for this process",
            filename,
            n_features,
            expected,
            fallback_filename,
        )
        models[filename] = fallback
        for cache in parallel_caches:
            if fallback_filename in cache:
                cache[filename] = cache[fallback_filename]


# --- Training-time categorical encoding, recovered and reused at inference ---
#
# train_models.encode_categoricals fits driver_id_encoded/circuit_id_encoded via
# pd.Categorical(...).codes fresh per training run and never persisted the
# resulting category list — strategy_service.py's and prediction_worker.py's
# module docstrings both flagged this as an unresolved gap, and inference
# substituted a deterministic crc32 hash as a stand-in (self-consistent, but
# not the code the model actually trained on). Confirmed via
# scripts/evaluate_driver_features.py's offline holdout comparison (see
# CLAUDE.md's Deferred Wiring entry) that scoring a real fitted model with the
# crc32 substitute instead of its own training codes inflates tire_deg holdout
# MAE by 50-265% depending on compound — this is a live, active production
# accuracy problem, not a theoretical one.
#
# Fix: train_models.py (and retrain_incremental.py) now recover the actual
# pd.Categorical code map via build_categorical_encoding_maps and embed it
# directly in EACH tire_deg model's own sidecar (train_models.py's
# serialize_evaluate_and_upload metrics dict) — not one shared artifact,
# since item 9's promotion guard promotes each compound independently, so two
# compounds' production models can come from different training runs with
# different driver/circuit universes. Keyed by circuit NAME rather than
# circuit id: retrain_incremental.py's current-season fetch path has no DB
# access and therefore no circuit UUID, while every code path (both training
# scripts, both inference workers) already resolves a circuit's display name.
# A missing map (legacy sidecar predating this fix) or a missing individual
# id (a driver/circuit that debuted after a model's last training run) both
# fall back to the same crc32 formula the pre-fix code used everywhere —
# this makes the fix strictly non-regressive: worst case is identical to
# today for exactly the ids it can't do better for.


@dataclass(frozen=True)
class CategoricalEncodingMaps:
    """One tire_deg model's own recovered training-time category code maps.

    Lives inside that model's sidecar (train_models.py's
    serialize_evaluate_and_upload metrics dict → the {tag}/{filename}.metrics.json
    S3 object), loaded alongside the model itself by each service's
    _load_models() and cached per filename — see resolve_driver_code/
    resolve_circuit_code below for how these are used.
    """

    driver_id_to_code: dict[str, int]
    circuit_name_to_code: dict[str, int]


def build_categorical_encoding_maps(df: pd.DataFrame) -> dict[str, dict[str, int]]:
    """Recover the driver_id/circuit_name -> pd.Categorical code maps a training run fit.

    Called once per training run (train_models.train_all / retrain_incremental.retrain),
    right after train_models.encode_categoricals — every tire_deg compound trained from
    that same encoded frame shares an identical map, since encode_categoricals fits the
    codes once across the whole combined frame before per-compound filtering.

    Args:
        df: A laps frame already run through encode_categoricals — must include driver_id,
            driver_id_encoded, circuit_name, circuit_id_encoded.
    Returns:
        {"driver_id_to_code": {str(driver_id): code}, "circuit_name_to_code": {circuit_name:
        code}} — plain-str-keyed, plain-int-valued (not numpy scalars) so this is directly
        JSON-serializable for embedding in a model sidecar's metrics dict, matching how
        n_features/feature_names are already embedded there (item 9).
    """
    driver_unique = df[["driver_id", "driver_id_encoded"]].drop_duplicates()
    driver_map = {
        str(driver_id): int(code)
        for driver_id, code in zip(
            driver_unique["driver_id"], driver_unique["driver_id_encoded"], strict=True
        )
    }
    circuit_unique = df[["circuit_name", "circuit_id_encoded"]].drop_duplicates()
    circuit_map = {
        str(circuit_name): int(code)
        for circuit_name, code in zip(
            circuit_unique["circuit_name"], circuit_unique["circuit_id_encoded"], strict=True
        )
    }
    return {"driver_id_to_code": driver_map, "circuit_name_to_code": circuit_map}


def encoding_maps_from_metrics(metrics: dict[str, Any] | None) -> CategoricalEncodingMaps | None:
    """Recover CategoricalEncodingMaps from a downloaded model sidecar's metrics dict.

    Args:
        metrics: A tire_deg model's own {filename}.metrics.json contents (see
            train_models.download_metrics for the sidecar-fetch pattern this complements),
            or None if no sidecar could be fetched at all.
    Returns:
        CategoricalEncodingMaps if both driver_id_to_code and circuit_name_to_code are
        present and are dicts, else None. A legacy sidecar (predates this fix) or a
        non-tire_deg model's sidecar (pit_predictor.pkl/safety_car_model.pkl never carry
        these keys) both correctly resolve to None — callers must treat None as "use the
        crc32 fallback for every id," not as an error.
    """
    if metrics is None:
        return None
    driver_map = metrics.get("driver_id_to_code")
    circuit_map = metrics.get("circuit_name_to_code")
    if not isinstance(driver_map, dict) or not isinstance(circuit_map, dict):
        return None
    return CategoricalEncodingMaps(driver_id_to_code=driver_map, circuit_name_to_code=circuit_map)


def _crc32_fallback_code(value: str, modulus: int = 1000) -> int:
    """The pre-fix deterministic stand-in, numerically identical to strategy_service's/
    prediction_worker's old duplicated `_stable_code`.

    Used by resolve_driver_code/resolve_circuit_code only when no persisted training-time
    code is available for a given id — kept bit-for-bit identical to the function it
    replaces so behavior only ever improves (more ids get a real code), never regresses
    for ids that had no real code recoverable either way.

    Args:
        value: The id (driver_id UUID string, or circuit name) to encode.
        modulus: Range to fold the hash into.
    Returns:
        A stable integer in [0, modulus).
    """
    return zlib.crc32(value.encode()) % modulus


def resolve_driver_code(maps: CategoricalEncodingMaps | None, driver_id: str) -> int:
    """The tire_deg feature vector's driver_id_encoded value for one driver.

    Prefers the real training-time pd.Categorical code recovered from the currently-loaded
    model's own sidecar — the code that model's driver_id_encoded feature was actually fit
    against. Falls back to a deterministic hash (see _crc32_fallback_code) for a driver
    missing from the map, or when maps itself is None (no sidecar at all).

    Args:
        maps: This compound's CategoricalEncodingMaps, or None if unavailable.
        driver_id: The driver's id, stringified the same way build_categorical_encoding_maps
            built the map (str(uuid.UUID)).
    Returns:
        The integer driver_id_encoded feature value.
    """
    if maps is not None:
        code = maps.driver_id_to_code.get(driver_id)
        if code is not None:
            return code
    return _crc32_fallback_code(driver_id)


def resolve_circuit_code(maps: CategoricalEncodingMaps | None, circuit_name: str) -> int:
    """The tire_deg feature vector's circuit_id_encoded value for one circuit.

    Mirrors resolve_driver_code exactly, keyed by circuit display name rather than id — see
    this module's build_categorical_encoding_maps docstring for why name is the key.

    Args:
        maps: This compound's CategoricalEncodingMaps, or None if unavailable.
        circuit_name: The circuit's display name (Circuit.name), matching how the map was built.
    Returns:
        The integer circuit_id_encoded feature value.
    """
    if maps is not None:
        code = maps.circuit_name_to_code.get(circuit_name)
        if code is not None:
            return code
    return _crc32_fallback_code(circuit_name)


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
