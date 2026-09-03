"""Offline evaluation of candidate per-driver features (CLAUDE.md deferred item 5).

Answers two questions with real holdout numbers, before any model is retrained or
uploaded anywhere:

1. Does a genuine per-driver signal improve the tyre-degradation and pit-predictor
   models, and by how much? `driver_id_encoded` (the only per-driver feature today)
   is an arbitrary category code with no relationship to driving ability — see
   CLAUDE.md's Deferred Wiring entry. This script trains each model with and
   without candidate replacements and compares holdout error.

2. What does the training-vs-inference encoding mismatch actually cost? Training
   encodes driver/circuit via `pd.Categorical(...).codes` (0..N-1, never persisted);
   inference substitutes `zlib.crc32(...) % 1000` because the training-time codes
   can't be recovered (see strategy_service.py's module docstring). This script
   scores one fitted model against the same holdout twice — once with the codes it
   was trained on, once with the codes inference actually supplies — so the gap is
   measured rather than assumed.

Nothing here writes to S3, promotes a model, or touches the database beyond a
read. Cross-validation is deliberately skipped (train_models.py's GroupKFold is
for reporting a promoted model's stability, not for ranking candidates) — every
variant is fit once on the same train split and scored on the same holdout split,
which is what makes the comparison apples-to-apples and keeps the run tractable.

Candidate features are computed **from prior sessions only** — a driver's value
for a session is the expanding mean over the races they had already completed
before it, chronologically by (season, round_number). Both candidates are derived
from `lap_time_delta`, which is the tyre model's own training target, so a
same-session aggregate would leak the target directly. Prior-sessions-only also
matches what inference can actually know at prediction time.

Run via: python -m backend.scripts.evaluate_driver_features
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from xgboost import XGBRegressor

from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData
from backend.scripts.train_models import (
    COMPOUND_TO_FILENAME,
    HOLDOUT_SEASON,
    TRAIN_SEASON_END,
    TRAIN_SEASON_START,
    add_safety_car_probability,
    encode_categoricals,
    fetch_stints_from_db,
    split_train_holdout,
)
from backend.services.ml import pit_predictor, safety_car_model, tire_deg_model

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

RESULTS_PATH = Path("driver_feature_evaluation.json")

# A (session, driver) pair needs enough valid laps for a per-session degradation
# slope to mean anything — a driver who retired on lap 3 contributes noise, not
# signal. 10 is well below a real stint length and still excludes early DNFs.
MIN_SESSION_LAPS = 10

# Per-session delta outlier trim before fitting that session's slope. A lap behind
# a Safety Car or stuck in traffic is tens of seconds off the driver's median and
# would dominate an ordinary least-squares slope, even though it says nothing
# about how that driver treats a tyre. Trimming symmetrically at these quantiles
# keeps the slope a pace-degradation measure rather than an incident detector.
DELTA_TRIM_LOW = 0.05
DELTA_TRIM_HIGH = 0.95

DEG_SENSITIVITY_COLUMN = "driver_deg_sensitivity"
CONSISTENCY_COLUMN = "driver_lap_time_consistency"

# Feature-set variants compared for the tyre-degradation models. "baseline" is
# exactly tire_deg_model.FEATURE_COLUMNS as deployed today.
TIRE_DEG_VARIANTS: dict[str, list[str]] = {
    "baseline": list(tire_deg_model.FEATURE_COLUMNS),
    "deg_sensitivity": [*tire_deg_model.FEATURE_COLUMNS, DEG_SENSITIVITY_COLUMN],
    "consistency": [*tire_deg_model.FEATURE_COLUMNS, CONSISTENCY_COLUMN],
    "both": [*tire_deg_model.FEATURE_COLUMNS, DEG_SENSITIVITY_COLUMN, CONSISTENCY_COLUMN],
}

PIT_VARIANTS: dict[str, list[str]] = {
    "baseline": list(pit_predictor.FEATURE_COLUMNS),
    "deg_sensitivity": [*pit_predictor.FEATURE_COLUMNS, DEG_SENSITIVITY_COLUMN],
    "consistency": [*pit_predictor.FEATURE_COLUMNS, CONSISTENCY_COLUMN],
    "both": [*pit_predictor.FEATURE_COLUMNS, DEG_SENSITIVITY_COLUMN, CONSISTENCY_COLUMN],
}


async def fetch_eval_laps() -> pd.DataFrame:
    """Fetch 2018-2025 laps with the extra context this evaluation needs.

    Deliberately a separate query from train_models.fetch_laps_from_db rather than
    a widening of it: this script is an offline experiment and must not change the
    production training path or the S3 parquet schema before its own results
    justify doing so. The three extra columns beyond that query are driver_code
    (a stable per-driver key, unlike the UUID the DB uses and the 3-letter code
    retrain_incremental.py's FastF1 path uses), round_number (chronological
    ordering within a season, for the prior-sessions-only aggregation), and
    circuit_id (the UUID inference hashes, needed to reproduce what inference
    actually feeds the model).

    Args: None.
    Returns:
        One row per lap_data row in [TRAIN_SEASON_START, HOLDOUT_SEASON] with a
        recorded lap time.
    """
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    query = (
        select(
            LapData.session_id,
            LapData.driver_id,
            Driver.code.label("driver_code"),
            LapData.lap_number,
            LapData.lap_time_seconds,
            LapData.compound,
            LapData.tyre_age_laps,
            LapData.position,
            LapData.track_status,
            LapData.track_temp,
            LapData.air_temp,
            LapData.is_valid,
            Race.season,
            Race.round_number,
            Race.circuit_id,
            Circuit.name.label("circuit_name"),
        )
        .join(SessionModel, LapData.session_id == SessionModel.id)
        .join(Race, SessionModel.race_id == Race.id)
        .join(Circuit, Race.circuit_id == Circuit.id)
        .join(Driver, LapData.driver_id == Driver.id)
        .where(
            Race.season.between(TRAIN_SEASON_START, HOLDOUT_SEASON),
            LapData.lap_time_seconds.is_not(None),
        )
    )
    async with session_factory() as db:
        rows = (await db.execute(query)).all()

    return pd.DataFrame(
        rows,
        columns=[
            "session_id",
            "driver_id",
            "driver_code",
            "lap_number",
            "lap_time_seconds",
            "compound",
            "tyre_age_laps",
            "position",
            "track_status",
            "track_temp",
            "air_temp",
            "is_valid",
            "season",
            "round_number",
            "circuit_id",
            "circuit_name",
        ],
    )


def per_session_driver_stats(laps: pd.DataFrame) -> pd.DataFrame:
    """Per (session, driver) degradation slope and lap-time consistency.

    Both are computed from lap_time_delta (each lap's deviation from that driver's
    own median in that session — tire_deg_model.add_engineered_features' own
    definition), on valid timed laps only, after trimming each pair's delta
    distribution to [DELTA_TRIM_LOW, DELTA_TRIM_HIGH]. The slope is the ordinary
    least-squares coefficient of delta on tyre_age_laps, computed in closed form
    from per-group sums so the whole corpus is one vectorized pass.

    Args:
        laps: Output of fetch_eval_laps.
    Returns:
        One row per (session_id, driver_code) with at least MIN_SESSION_LAPS
        trimmed laps and a non-degenerate tyre-age spread: session_id,
        driver_code, season, round_number, deg_sensitivity (seconds of delta per
        lap of tyre age), consistency (standard deviation of delta, seconds).
    """
    df = laps[laps["is_valid"] & laps["lap_time_seconds"].notna()].copy()
    keys = ["session_id", "driver_code"]
    df["delta"] = df["lap_time_seconds"] - df.groupby(keys)["lap_time_seconds"].transform("median")

    bounds = df.groupby(keys)["delta"].quantile([DELTA_TRIM_LOW, DELTA_TRIM_HIGH]).unstack()
    bounds.columns = ["delta_low", "delta_high"]
    df = df.merge(bounds, left_on=keys, right_index=True, how="left")
    df = df[(df["delta"] >= df["delta_low"]) & (df["delta"] <= df["delta_high"])]

    df["_x"] = df["tyre_age_laps"].astype(float)
    df["_y"] = df["delta"].astype(float)
    df["_xy"] = df["_x"] * df["_y"]
    df["_xx"] = df["_x"] ** 2

    grouped = df.groupby(keys)
    agg = grouped[["_x", "_y", "_xy", "_xx"]].sum()
    agg["n"] = grouped.size()
    agg["consistency"] = grouped["_y"].std()
    agg[["season", "round_number"]] = grouped[["season", "round_number"]].first()

    denominator = agg["n"] * agg["_xx"] - agg["_x"] ** 2
    agg["deg_sensitivity"] = (agg["n"] * agg["_xy"] - agg["_x"] * agg["_y"]) / denominator

    agg = agg[(agg["n"] >= MIN_SESSION_LAPS) & (denominator != 0)]
    return agg.reset_index()[
        ["session_id", "driver_code", "season", "round_number", "deg_sensitivity", "consistency"]
    ]


def prior_session_means(stats: pd.DataFrame, train_seasons: set[int]) -> pd.DataFrame:
    """Expanding mean of each per-session stat over that driver's *earlier* races.

    A driver's value for session k is the mean of their per-session values for
    sessions 1..k-1, ordered by (season, round_number) — never including session k
    itself. Both stats are functions of lap_time_delta, which is the tyre model's
    training target, so including the current session would leak the target into
    the feature. A driver's first-ever session has no prior races and falls back to
    the corpus mean, computed over training seasons only so no holdout-season
    information reaches a training row through the fallback either.

    Args:
        stats: Output of per_session_driver_stats.
        train_seasons: Seasons the fallback mean may be computed from.
    Returns:
        One row per (session_id, driver_code) with DEG_SENSITIVITY_COLUMN and
        CONSISTENCY_COLUMN, NaN-free.
    """
    ordered = stats.sort_values(["driver_code", "season", "round_number"]).copy()
    source_to_feature = {
        "deg_sensitivity": DEG_SENSITIVITY_COLUMN,
        "consistency": CONSISTENCY_COLUMN,
    }
    train_rows = stats[stats["season"].isin(train_seasons)]
    for source, feature in source_to_feature.items():
        prior = ordered.groupby("driver_code")[source].transform(
            lambda s: s.expanding().mean().shift(1)
        )
        ordered[feature] = prior.fillna(float(train_rows[source].mean()))
    return ordered[["session_id", "driver_code", *source_to_feature.values()]]


def attach_driver_features(laps: pd.DataFrame, prior_means: pd.DataFrame) -> pd.DataFrame:
    """Merge the prior-sessions-only per-driver features onto every lap row.

    Args:
        laps: Output of fetch_eval_laps (or any frame keyed by session_id/driver_code).
        prior_means: Output of prior_session_means.
    Returns:
        Copy of laps with DEG_SENSITIVITY_COLUMN and CONSISTENCY_COLUMN added.
        A (session, driver) pair excluded by per_session_driver_stats' minimum-lap
        filter has no prior_means row of its own; it falls back to the corpus mean
        of the merged column, matching prior_session_means' own fallback.
    """
    merged = laps.merge(prior_means, on=["session_id", "driver_code"], how="left")
    for column in (DEG_SENSITIVITY_COLUMN, CONSISTENCY_COLUMN):
        merged[column] = merged[column].fillna(float(prior_means[column].mean()))
    return merged


def _stable_code(value: str, modulus: int = 1000) -> int:
    """The exact encoding inference substitutes for an unrecoverable training code.

    Copied verbatim from strategy_service._stable_code / prediction_worker
    ._stable_code (both identical) so the mismatch measured below is the real one,
    not an approximation of it.

    Args:
        value: The id (circuit_id or driver_id, stringified) to encode.
        modulus: Range to fold the hash into.
    Returns:
        A stable integer in [0, modulus).
    """
    return zlib.crc32(value.encode()) % modulus


def _build_regressor() -> Pipeline:
    """A pipeline identical to tire_deg_model._build_pipeline, minus its CV wrapper."""
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "xgb",
                XGBRegressor(n_estimators=300, learning_rate=0.05, max_depth=6, random_state=42),
            ),
        ]
    )


def _fit_and_score(
    train: pd.DataFrame, holdout: pd.DataFrame, features: list[str], target: str
) -> tuple[Pipeline, float]:
    """Fit one regressor on train and return it with its holdout MAE.

    Args:
        train: Feature-engineered training rows.
        holdout: Feature-engineered holdout rows, same columns.
        features: Feature column names, in order.
        target: Target column name.
    Returns:
        (fitted pipeline, holdout mean absolute error).
    """
    pipeline = _build_regressor()
    pipeline.fit(train[features].to_numpy(dtype=float), train[target].to_numpy(dtype=float))
    predictions = pipeline.predict(holdout[features].to_numpy(dtype=float))
    return pipeline, float(np.mean(np.abs(predictions - holdout[target].to_numpy(dtype=float))))


def evaluate_tire_deg(
    train_laps: pd.DataFrame, holdout_laps: pd.DataFrame
) -> dict[str, dict[str, float | None]]:
    """Holdout MAE per compound for every TIRE_DEG_VARIANTS feature set.

    Args:
        train_laps: Encoded, driver-feature-attached training laps (is_valid only).
        holdout_laps: The same for the holdout season.
    Returns:
        {compound: {variant: holdout_mae}}. A compound with no holdout rows maps
        every variant to None — no comparison is possible, and reporting a
        cv-only substitute here would not be comparable across variants.
    """
    results: dict[str, dict[str, float | None]] = {}
    for compound in COMPOUND_TO_FILENAME:
        compound_train = tire_deg_model.add_engineered_features(
            train_laps[train_laps["compound"] == compound]
        )
        compound_holdout = tire_deg_model.add_engineered_features(
            holdout_laps[holdout_laps["compound"] == compound]
        )
        if compound_train.empty:
            logger.warning("Skipping %s: no training rows", compound)
            continue
        if compound_holdout.empty:
            logger.warning("%s: no holdout rows in season %d", compound, HOLDOUT_SEASON)
            results[compound] = dict.fromkeys(TIRE_DEG_VARIANTS)
            continue

        results[compound] = {}
        for variant, features in TIRE_DEG_VARIANTS.items():
            _, mae = _fit_and_score(
                compound_train, compound_holdout, features, tire_deg_model.TARGET_COLUMN
            )
            results[compound][variant] = mae
            logger.info(
                "tire_deg %s / %s: holdout MAE=%.5f (n=%d)",
                compound,
                variant,
                mae,
                len(compound_train),
            )
    return results


def evaluate_encoding_mismatch(
    train_laps: pd.DataFrame, holdout_laps: pd.DataFrame
) -> dict[str, dict[str, float]]:
    """Cost of scoring a baseline model with the codes inference actually supplies.

    Fits the deployed 6-feature schema on the training codes, then scores the same
    holdout twice: once with those codes, once with driver_id_encoded and
    circuit_id_encoded replaced by _stable_code(...) of the same UUIDs inference
    hashes. The difference is what the unpersisted-encoding gap costs in practice.

    Args:
        train_laps: Encoded training laps (is_valid only).
        holdout_laps: The same for the holdout season.
    Returns:
        {compound: {"trained_codes": mae, "inference_codes": mae}}.
    """
    results: dict[str, dict[str, float]] = {}
    features = list(tire_deg_model.FEATURE_COLUMNS)
    for compound in COMPOUND_TO_FILENAME:
        compound_train = tire_deg_model.add_engineered_features(
            train_laps[train_laps["compound"] == compound]
        )
        compound_holdout = tire_deg_model.add_engineered_features(
            holdout_laps[holdout_laps["compound"] == compound]
        )
        if compound_train.empty or compound_holdout.empty:
            continue

        pipeline, trained_mae = _fit_and_score(
            compound_train, compound_holdout, features, tire_deg_model.TARGET_COLUMN
        )

        as_inference = compound_holdout.copy()
        as_inference["driver_id_encoded"] = [
            _stable_code(str(value)) for value in as_inference["driver_id"]
        ]
        as_inference["circuit_id_encoded"] = [
            _stable_code(str(value)) for value in as_inference["circuit_id"]
        ]
        predictions = pipeline.predict(as_inference[features].to_numpy(dtype=float))
        target = as_inference[tire_deg_model.TARGET_COLUMN].to_numpy(dtype=float)
        inference_mae = float(np.mean(np.abs(predictions - target)))

        results[compound] = {"trained_codes": trained_mae, "inference_codes": inference_mae}
        logger.info(
            "encoding mismatch %s: trained codes MAE=%.5f vs inference codes MAE=%.5f (%+.1f%%)",
            compound,
            trained_mae,
            inference_mae,
            100.0 * (inference_mae - trained_mae) / trained_mae,
        )
    return results


def _life_remaining_for_variant(
    df: pd.DataFrame, pipelines: dict[str, Pipeline], features: list[str]
) -> pd.Series:
    """predicted_life_remaining for every row, for an arbitrary tire_deg feature set.

    Local re-implementation of train_models.add_predicted_life_remaining +
    tire_deg_model.predict_life_remaining_batch, generalised over the feature list
    so a candidate 7- or 8-feature tyre model can be used here without changing
    either production module — this script must stay side-effect-free until its own
    results justify a schema change. The per-driver candidate features are constant
    across the lookahead window (they describe the driver, not the lap), so they are
    simply repeated alongside the other held-constant columns.

    Args:
        df: Rows with every column in features, plus lap_number and tyre_age_laps.
        pipelines: Fitted tyre-degradation pipeline per compound.
        features: The feature list those pipelines were fit on, in order.
    Returns:
        Series aligned to df.index: laps until predicted delta crosses
        tire_deg_model.DEGRADATION_THRESHOLD_SECONDS, capped at MAX_LOOKAHEAD_LAPS.
    """
    lookahead = tire_deg_model.MAX_LOOKAHEAD_LAPS
    offsets = np.arange(lookahead)
    out = pd.Series(lookahead, index=df.index, dtype=np.int64)

    for compound, group in df.groupby("compound"):
        pipeline = pipelines.get(str(compound))
        if pipeline is None:
            continue
        n = len(group)
        columns = []
        for feature in features:
            values = group[feature].to_numpy(dtype=float)
            if feature in ("lap_number", "tyre_age_laps"):
                columns.append((values[:, None] + offsets[None, :]).ravel())
            else:
                columns.append(np.repeat(values, lookahead))
        matrix = np.stack(columns, axis=1)
        predictions = pipeline.predict(matrix).reshape(n, lookahead)
        crossed = predictions >= tire_deg_model.DEGRADATION_THRESHOLD_SECONDS
        first_cross = np.argmax(crossed, axis=1)
        first_cross[~crossed.any(axis=1)] = lookahead
        out.loc[group.index] = first_cross
    return out


def evaluate_pit_predictor(
    train_laps: pd.DataFrame,
    holdout_laps: pd.DataFrame,
    pit_train_laps: pd.DataFrame,
    pit_holdout_laps: pd.DataFrame,
    stints: pd.DataFrame,
) -> dict[str, dict[str, float]]:
    """Holdout MAE and AUC per PIT_VARIANTS feature set.

    Each variant retrains the tyre-degradation models on the *matching* feature set
    first, because predicted_life_remaining is a cross-model feature — comparing a
    candidate pit model against a baseline one while both consume the same baseline
    life-remaining estimate would understate the candidate. This mirrors how
    train_models.train_all composes the two models.

    Args:
        train_laps: Encoded, feature-attached, is_valid-only training laps.
        holdout_laps: The same for the holdout season.
        pit_train_laps: Encoded, feature-attached training laps *including* invalid
            ones (pit/in/out laps are the positive class).
        pit_holdout_laps: The same for the holdout season.
        stints: tire_stints rows, for pit-lap labelling.
    Returns:
        {variant: {"holdout_mae": float, "holdout_auc": float}}.
    """
    sc_model = safety_car_model.train_safety_car_model(safety_car_model.build_lap_flags(train_laps))

    pit_train = tire_deg_model.add_engineered_features(
        pit_predictor.prepare_pit_predictor_features(pit_train_laps, stints)
    )
    pit_holdout = tire_deg_model.add_engineered_features(
        pit_predictor.prepare_pit_predictor_features(pit_holdout_laps, stints)
    )
    pit_train["safety_car_probability"] = add_safety_car_probability(pit_train, sc_model)
    pit_holdout["safety_car_probability"] = add_safety_car_probability(pit_holdout, sc_model)

    results: dict[str, dict[str, float]] = {}
    for variant, pit_features in PIT_VARIANTS.items():
        tire_features = TIRE_DEG_VARIANTS[variant]
        pipelines: dict[str, Pipeline] = {}
        for compound in COMPOUND_TO_FILENAME:
            compound_train = tire_deg_model.add_engineered_features(
                train_laps[train_laps["compound"] == compound]
            )
            if compound_train.empty:
                continue
            pipeline = _build_regressor()
            pipeline.fit(
                compound_train[tire_features].to_numpy(dtype=float),
                compound_train[tire_deg_model.TARGET_COLUMN].to_numpy(dtype=float),
            )
            pipelines[compound] = pipeline

        train_frame = pit_train.copy()
        holdout_frame = pit_holdout.copy()
        train_frame["predicted_life_remaining"] = _life_remaining_for_variant(
            train_frame, pipelines, tire_features
        )
        holdout_frame["predicted_life_remaining"] = _life_remaining_for_variant(
            holdout_frame, pipelines, tire_features
        )

        target = train_frame[pit_predictor.TARGET_COLUMN].to_numpy(dtype=int)
        positive_rate = float(target.mean())
        model = pit_predictor._build_model(
            (1 - positive_rate) / positive_rate if positive_rate > 0 else 1.0
        )
        model.fit(train_frame[pit_features].to_numpy(dtype=float), target)

        holdout_target = holdout_frame[pit_predictor.TARGET_COLUMN].to_numpy(dtype=float)
        probabilities = model.predict_proba(holdout_frame[pit_features].to_numpy(dtype=float))[:, 1]
        results[variant] = {
            "holdout_mae": float(np.mean(np.abs(probabilities - holdout_target))),
            "holdout_auc": float(roc_auc_score(holdout_target, probabilities)),
        }
        logger.info(
            "pit_predictor / %s: holdout MAE=%.5f AUC=%.5f",
            variant,
            results[variant]["holdout_mae"],
            results[variant]["holdout_auc"],
        )
    return results


def describe_feature_spread(prior_means: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Summary statistics for each candidate feature, for the write-up.

    Args:
        prior_means: Output of prior_session_means.
    Returns:
        {feature: {"min": .., "max": .., "mean": .., "std": ..}}.
    """
    return {
        column: {
            "min": float(prior_means[column].min()),
            "max": float(prior_means[column].max()),
            "mean": float(prior_means[column].mean()),
            "std": float(prior_means[column].std()),
        }
        for column in (DEG_SENSITIVITY_COLUMN, CONSISTENCY_COLUMN)
    }


async def evaluate(skip_pit: bool) -> dict[str, object]:
    """Run the full evaluation and return every result as one JSON-ready dict.

    Args:
        skip_pit: Skip the pit_predictor comparison (the slowest stage — it
            retrains all five tyre models per variant).
    Returns:
        Dict with driver_feature_spread, tire_deg, encoding_mismatch and
        (unless skipped) pit_predictor sections.
    """
    logger.info("Fetching laps (%d-%d)...", TRAIN_SEASON_START, HOLDOUT_SEASON)
    raw_laps = await fetch_eval_laps()
    stints = await fetch_stints_from_db()
    await get_engine().dispose()
    logger.info("Fetched %d lap row(s), %d stint row(s)", len(raw_laps), len(stints))

    train_seasons = set(range(TRAIN_SEASON_START, TRAIN_SEASON_END + 1))
    stats = per_session_driver_stats(raw_laps)
    logger.info("Computed per-session stats for %d (session, driver) pair(s)", len(stats))
    prior_means = prior_session_means(stats, train_seasons)
    raw_laps = attach_driver_features(raw_laps, prior_means)

    laps = raw_laps[raw_laps["is_valid"]].drop(columns=["is_valid"]).copy()
    laps["laps_in_session"] = laps.groupby("session_id")["lap_number"].transform("max")
    laps = encode_categoricals(laps)
    train_laps, holdout_laps = split_train_holdout(laps)
    logger.info("Train laps: %d, holdout laps: %d", len(train_laps), len(holdout_laps))

    results: dict[str, object] = {
        "driver_feature_spread": describe_feature_spread(prior_means),
        "tire_deg": evaluate_tire_deg(train_laps, holdout_laps),
        "encoding_mismatch": evaluate_encoding_mismatch(train_laps, holdout_laps),
    }

    if not skip_pit:
        pit_laps = raw_laps.drop(columns=["is_valid"]).copy()
        pit_laps["laps_in_session"] = pit_laps.groupby("session_id")["lap_number"].transform("max")
        pit_laps = encode_categoricals(pit_laps)
        pit_train_laps, pit_holdout_laps = split_train_holdout(pit_laps)
        results["pit_predictor"] = evaluate_pit_predictor(
            train_laps, holdout_laps, pit_train_laps, pit_holdout_laps, stints
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-pit",
        action="store_true",
        help="Skip the pit_predictor comparison (the slowest stage).",
    )
    args = parser.parse_args()

    results = asyncio.run(evaluate(skip_pit=args.skip_pit))
    RESULTS_PATH.write_text(json.dumps(results, indent=2, default=str))
    logger.info("Wrote %s", RESULTS_PATH)


if __name__ == "__main__":
    main()
