"""Offline validation of pit_predictor's label-definition fix (core-feature-rebuild
Checkpoint 6).

Answers the checkpoint's own question with real numbers, before touching S3 or
production: does relabeling did_pit_this_lap -> pit_within_k_laps (services/ml/
pit_predictor.py's label_pit_laps, already fixed by this point in the checkpoint)
actually turn a same-lap detector into a genuine advance-warning signal?

Train/holdout split is train_models.py's own split_train_holdout, used with its
DEFAULTS (2018-2024 train, 2025 holdout) — the project's established convention
for every other model in this codebase (tire_deg, item 9's promotion-guard
testing, etc.), not a bespoke range for this script. An earlier version of this
script folded 2025 into training and used 2026 as if it were "the holdout" —
that was a genuine bug (conflated train_models.fetch_laps_from_db's FETCH range,
2018-2025, with its actual TRAIN range, 2018-2024; see the now-corrected cv_auc/
holdout_mae numbers this produced vs. before). 2026 is fetched ADDITIONALLY,
strictly as a third, separate slice — never part of train OR the standard
holdout — used ONLY for the per-driver trajectory check below, which is
therefore doubly out-of-sample (the model has seen neither 2025 nor 2026).

Two things are compared, both trained on the SAME real local corpus/split and
the SAME real (already-promoted) production tire_deg/safety_car models for the
predicted_life_remaining/safety_car_probability cross-features — deliberately
NOT retrained here, since this checkpoint changes nothing about those two model
families and reusing the real production ones is more representative of actual
inference than a fresh local fit would be:

1. cv_auc/positive_rate/holdout_mae for OLD-label vs. NEW-label pit_predictor,
   evaluated against the STANDARD 2025 holdout — comparable to this project's
   usual benchmark (though still not directly comparable to production's own
   recorded metrics, since this script's train set additionally includes
   invalid/pit laps production's pace-only comparisons wouldn't — see
   train_models.py's own note on why pit_predictor is trained differently).
2. Per-lap predicted probability, OLD model vs. NEW model, across the laps
   leading up to 3 REAL pit stops from the exact session (Belgian GP 2026 Round
   10, da57b9fd-4976-4fce-91a1-c7d0aac9c619) the original investigation's own
   evidence table was measured against — LEC/COL/GAS. This is the actual
   deliverable: does the NEW model's probability rise across laps BEFORE the
   pit, not just spike on it. Both models here are the SAME ones evaluated in
   (1) above (trained on 2018-2024 only) — this is an additional check on top
   of the standard holdout, not a different model.

"Pit lap" here means the LAST lap on the old tyre (the lap the car is
physically in the pit lane) — one lap before a stint's own start_lap (the
OUT-lap, first lap on the fresh tyre). Same convention strategy_service.
build_pit_recommendation/pit_predictor.label_pit_laps both use. Note this
differs from how the original investigation's own evidence table informally
cited "real pit lap N" (it used start_lap itself, the out-lap) — this script
reports both lap numbers explicitly per driver to avoid any ambiguity.

Nothing here writes to S3, promotes a model, or modifies the database.

Run via: python -m backend.scripts.evaluate_pit_predictor_label_fix
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, cast

import boto3
import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.pipeline import Pipeline
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_aws_settings, get_ml_settings
from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData, TireStint
from backend.scripts.train_models import (
    COMPOUND_TO_FILENAME,
    HOLDOUT_SEASON,
    TRAIN_SEASON_END,
    TRAIN_SEASON_START,
    add_predicted_life_remaining,
    add_safety_car_probability,
    encode_categoricals,
    split_train_holdout,
)
from backend.services.ml import pit_predictor, tire_deg_model

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Extends train_models.py's fetch range (fetch_laps_from_db's own
# TRAIN_SEASON_START..HOLDOUT_SEASON = 2018-2025) by one season — the real pit
# stops the trajectory check validates against are 2026 data. 2026 is used
# ONLY as a third, separate slice below (never train, never the standard
# holdout) — see module docstring.
FETCH_SEASON_END = 2026
TRAJECTORY_SEASON = 2026

_MODEL_VERSION_TAG = "production"

# The exact session/drivers/pit stops the original investigation's own
# evidence table was measured against (docs/core-feature-rebuild-strategy-
# recommendations.md §2b) — Belgian GP 2026 Round 10.
VALIDATION_SESSION_ID = "da57b9fd-4976-4fce-91a1-c7d0aac9c619"
VALIDATION_DRIVER_CODES = ("LEC", "COL", "GAS")
# Laps shown before/after each driver's own pit lap.
TRAJECTORY_LAPS_BEFORE = 5
TRAJECTORY_LAPS_AFTER = 2


async def fetch_all_laps() -> pd.DataFrame:
    """Fetch all timed laps through FETCH_SEASON_END, including invalid ones.

    Same shape as train_models.fetch_laps_from_db, just a wider season range
    — see module docstring.
    """
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    query = (
        select(
            LapData.session_id,
            LapData.driver_id,
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
            Circuit.name.label("circuit_name"),
        )
        .join(SessionModel, LapData.session_id == SessionModel.id)
        .join(Race, SessionModel.race_id == Race.id)
        .join(Circuit, Race.circuit_id == Circuit.id)
        .where(
            Race.season.between(TRAIN_SEASON_START, FETCH_SEASON_END),
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
            "circuit_name",
        ],
    )


async def fetch_all_stints() -> pd.DataFrame:
    """Fetch tire_stints rows through FETCH_SEASON_END — see fetch_all_laps."""
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    query = (
        select(
            TireStint.session_id,
            TireStint.driver_id,
            TireStint.stint_number,
            TireStint.start_lap,
        )
        .join(SessionModel, TireStint.session_id == SessionModel.id)
        .join(Race, SessionModel.race_id == Race.id)
        .where(Race.season.between(TRAIN_SEASON_START, FETCH_SEASON_END))
    )
    async with session_factory() as db:
        rows = (await db.execute(query)).all()

    return pd.DataFrame(rows, columns=["session_id", "driver_id", "stint_number", "start_lap"])


async def fetch_driver_codes(driver_ids: set[Any]) -> dict[Any, str]:
    """driver_id -> Driver.code, for the validation drivers only."""
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    query = select(Driver.id, Driver.code).where(Driver.id.in_(driver_ids))
    async with session_factory() as db:
        rows = (await db.execute(query)).all()
    return {row[0]: row[1] for row in rows}


def load_production_models() -> tuple[dict[str, tire_deg_model.TireDegTrainResult], Any]:
    """Download the currently-promoted tire_deg_*.pkl (5x) + safety_car_model.pkl
    from S3, read-only — no retraining, matching what real inference actually
    uses for the predicted_life_remaining/safety_car_probability cross-features.

    Returns:
        (tire_deg_results keyed by compound name, safety_car_model instance).
        Each pipeline is wrapped in a TireDegTrainResult (add_predicted_life_
        remaining's expected shape, from train_models.py's own training-time
        return value) with dummy cv_mae/cv_rmse/n_samples — those 3 fields
        are never read by add_predicted_life_remaining, only .pipeline is.
    """
    settings = get_aws_settings()
    client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    model_dir = Path(get_ml_settings().model_cache_dir)
    model_dir.mkdir(parents=True, exist_ok=True)

    # Keyed by filename first (not compound), and aliased via
    # apply_incompatible_model_fallbacks before wrapping — same "WET's stale
    # 8-feature production model aliases to INTER's 6-feature one" guard
    # strategy_service.py/prediction_worker.py's own _load_models() apply.
    # Skipping this here would reproduce the exact crash that guard exists
    # for (a schema-mismatched StandardScaler), just in this script instead.
    pipelines_by_filename: dict[str, Any] = {}
    for filename in COMPOUND_TO_FILENAME.values():
        local_path = model_dir / filename
        if not local_path.exists():
            client.download_file(
                settings.aws_bucket_name, f"{_MODEL_VERSION_TAG}/{filename}", str(local_path)
            )
        pipelines_by_filename[filename] = joblib.load(local_path)
    tire_deg_model.apply_incompatible_model_fallbacks(pipelines_by_filename)

    tire_deg_results: dict[str, tire_deg_model.TireDegTrainResult] = {}
    for compound, filename in COMPOUND_TO_FILENAME.items():
        pipeline = cast(Pipeline, pipelines_by_filename[filename])
        tire_deg_results[compound] = tire_deg_model.TireDegTrainResult(
            pipeline=pipeline, cv_mae=0.0, cv_rmse=0.0, n_samples=0
        )

    sc_local_path = model_dir / "safety_car_model.pkl"
    if not sc_local_path.exists():
        client.download_file(
            settings.aws_bucket_name,
            f"{_MODEL_VERSION_TAG}/safety_car_model.pkl",
            str(sc_local_path),
        )
    sc_model = joblib.load(sc_local_path)

    return tire_deg_results, sc_model


def _old_label_pit_laps(laps: pd.DataFrame, stints: pd.DataFrame) -> pd.Series:
    """Byte-for-byte reproduction of pit_predictor.label_pit_laps' PRE-fix
    behavior (True only on a stint's own start_lap — the out-lap) — kept here,
    not in pit_predictor.py itself, purely so this script can train a genuine
    side-by-side comparison against the already-fixed module function below.
    """
    pit_laps = stints[
        stints["stint_number"]
        > stints.groupby(["session_id", "driver_id"])["stint_number"].transform("min")
    ][["session_id", "driver_id", "start_lap"]].rename(columns={"start_lap": "lap_number"})
    pit_lap_keys = pd.MultiIndex.from_frame(pit_laps[["session_id", "driver_id", "lap_number"]])
    row_keys = pd.MultiIndex.from_frame(laps[["session_id", "driver_id", "lap_number"]])
    return pd.Series(row_keys.isin(pit_lap_keys), index=laps.index, name="old_label")


def build_pit_frame(
    laps: pd.DataFrame,
    stints: pd.DataFrame,
    tire_deg_results: dict[str, Any],
    sc_model: Any,
) -> pd.DataFrame:
    """The full 8-feature pit_predictor frame, PLUS both the old and new label
    columns side by side, for one laps split (train or holdout).

    Args:
        laps: Encoded laps (via encode_categoricals), including invalid rows.
        stints: tire_stints rows for the same laps.
        tire_deg_results: Loaded production tire_deg pipelines, by compound.
        sc_model: Loaded production SafetyCarModel.
    Returns:
        DataFrame with FEATURE_COLUMNS, old_label, and pit_predictor.TARGET_COLUMN
        (the new label), plus session_id/lap_number/driver_id for the
        per-driver trajectory lookup later.
    """
    df = pit_predictor.prepare_pit_predictor_features(laps, stints)
    df = tire_deg_model.add_engineered_features(df)
    df["predicted_life_remaining"] = add_predicted_life_remaining(df, tire_deg_results)
    df["safety_car_probability"] = add_safety_car_probability(df, sc_model)
    # df's own session_id/driver_id/lap_number columns (not laps') — the
    # add_gap_features merges inside prepare_pit_predictor_features don't
    # preserve laps' original row index, so re-indexing back into laps by
    # df.index would silently misalign; df already carries these 3 key
    # columns directly, which is all _old_label_pit_laps needs.
    df["old_label"] = _old_label_pit_laps(df, stints)
    return df


def train_and_evaluate(
    train_df: pd.DataFrame, holdout_df: pd.DataFrame, label_column: str
) -> tuple[dict[str, float], LGBMClassifier]:
    """Train one pit_predictor variant on label_column, report cv_auc/holdout_mae.

    Args:
        train_df, holdout_df: Output of build_pit_frame.
        label_column: "old_label" or pit_predictor.TARGET_COLUMN — which
            column to train against.
    Returns:
        ({"cv_auc", "positive_rate", "holdout_mae", "n_samples"}, fitted model)
        — the model is returned too so print_trajectory can run predict_proba
        against it directly, without re-fitting.
    """
    train_for_fit = train_df.copy()
    holdout_for_fit = holdout_df.copy()
    if label_column != pit_predictor.TARGET_COLUMN:
        train_for_fit[pit_predictor.TARGET_COLUMN] = train_for_fit[label_column]
        holdout_for_fit[pit_predictor.TARGET_COLUMN] = holdout_for_fit[label_column]

    result = pit_predictor.train_pit_predictor(train_for_fit)
    holdout_mae = pit_predictor.evaluate_holdout(result.model, holdout_for_fit)
    metrics = {
        "cv_auc": result.cv_auc,
        "positive_rate": result.positive_rate,
        "holdout_mae": holdout_mae,
        "n_samples": float(result.n_samples),
    }
    return metrics, result.model


def print_trajectory(
    driver_code: str,
    driver_frame: pd.DataFrame,
    pit_lap: int,
    out_lap: int,
    old_model: Any,
    new_model: Any,
) -> None:
    """Print predicted probability, old vs. new model, for the laps around one
    driver's real pit stop — the qualitative deliverable this script exists for.
    """
    window = driver_frame[
        (driver_frame["lap_number"] >= pit_lap - TRAJECTORY_LAPS_BEFORE)
        & (driver_frame["lap_number"] <= pit_lap + TRAJECTORY_LAPS_AFTER)
    ].sort_values("lap_number")
    if window.empty:
        logger.warning("%s: no rows found around pit_lap=%d", driver_code, pit_lap)
        return

    features = window[pit_predictor.FEATURE_COLUMNS].to_numpy(dtype=float)
    old_probs = cast(np.ndarray, old_model.predict_proba(features))[:, 1]
    new_probs = cast(np.ndarray, new_model.predict_proba(features))[:, 1]

    print(f"\n{driver_code} — pit lap {pit_lap} (in-lap), out-lap {out_lap}:")
    print(f"{'lap':>4}  {'old_model':>10}  {'new_model':>10}  marker")
    for lap_number, old_p, new_p in zip(window["lap_number"], old_probs, new_probs, strict=True):
        marker = (
            " <- PIT LAP"
            if lap_number == pit_lap
            else (" <- out-lap" if lap_number == out_lap else "")
        )
        print(f"{lap_number:>4}  {old_p:>10.4f}  {new_p:>10.4f}{marker}")


async def run(skip_training: bool = False) -> None:
    logger.info("Fetching laps/stints (%d-%d)...", TRAIN_SEASON_START, FETCH_SEASON_END)
    raw_laps = await fetch_all_laps()
    stints = await fetch_all_stints()
    logger.info("Fetched %d lap row(s), %d stint row(s)", len(raw_laps), len(stints))

    pit_laps_all = raw_laps.drop(columns=["is_valid"]).copy()
    pit_laps_all["laps_in_session"] = pit_laps_all.groupby("session_id")["lap_number"].transform(
        "max"
    )
    pit_laps_all = encode_categoricals(pit_laps_all)

    # Standard project convention (train_models.split_train_holdout's own
    # defaults): train = 2018-2024, holdout = 2025. This is what old_metrics/
    # new_metrics below are evaluated against — comparable to every other
    # model in this codebase, not a bespoke range.
    train_laps, holdout_laps = split_train_holdout(pit_laps_all)
    # 2026 is a THIRD, independent slice — never in train, never in the
    # standard holdout above — used only for the trajectory check further
    # down, against real 2026 pit stops.
    trajectory_laps = pit_laps_all[pit_laps_all["season"] == TRAJECTORY_SEASON].copy()
    logger.info(
        "Train laps (seasons %d-%d): %d, standard holdout laps (season %d): %d, "
        "trajectory-only laps (season %d): %d",
        TRAIN_SEASON_START,
        TRAIN_SEASON_END,
        len(train_laps),
        HOLDOUT_SEASON,
        len(holdout_laps),
        TRAJECTORY_SEASON,
        len(trajectory_laps),
    )

    logger.info("Loading production tire_deg/safety_car models from S3 (read-only)...")
    tire_deg_results, sc_model = load_production_models()

    logger.info("Building pit_predictor feature frames (old + new labels)...")
    pit_train = build_pit_frame(train_laps, stints, tire_deg_results, sc_model)
    pit_holdout = build_pit_frame(holdout_laps, stints, tire_deg_results, sc_model)
    pit_trajectory = build_pit_frame(trajectory_laps, stints, tire_deg_results, sc_model)
    await get_engine().dispose()

    logger.info(
        "Positive rate — old label: %.4f, new label (K=%d): %.4f",
        pit_train["old_label"].mean(),
        pit_predictor.PIT_LABEL_HORIZON_LAPS,
        pit_train[pit_predictor.TARGET_COLUMN].mean(),
    )

    if skip_training:
        logger.info("--skip-training passed; not fitting models.")
        return

    logger.info("Training OLD-label pit_predictor...")
    old_metrics, old_model = train_and_evaluate(pit_train, pit_holdout, "old_label")
    logger.info("Training NEW-label pit_predictor...")
    new_metrics, new_model = train_and_evaluate(pit_train, pit_holdout, pit_predictor.TARGET_COLUMN)

    print(f"\n=== cv_auc / positive_rate / holdout_mae (standard {HOLDOUT_SEASON} holdout) ===")
    print(f"{'variant':<12}{'cv_auc':>10}{'positive_rate':>16}{'holdout_mae':>14}{'n_samples':>12}")
    for name, metrics in (("old_label", old_metrics), ("new_label", new_metrics)):
        print(
            f"{name:<12}{metrics['cv_auc']:>10.4f}{metrics['positive_rate']:>16.4f}"
            f"{metrics['holdout_mae']:>14.4f}{metrics['n_samples']:>12.0f}"
        )

    # --- Per-driver trajectory: the actual deliverable. ---
    # old_model/new_model above were trained on train_laps (2018-2024) only —
    # this trajectory check reuses those SAME fitted models against the 2026
    # slice, doubly out-of-sample (never seen 2025 or 2026).
    # session_id/driver_id columns are real uuid.UUID objects (Postgres UUID
    # columns deserialize that way via SQLAlchemy's UUID(as_uuid=True)) — the
    # plain string constants above must be parsed to UUID before comparing,
    # or every row-selection below silently matches nothing (a UUID never
    # equals a string, no exception raised either) — confirmed live on a
    # first run: all 3 drivers logged "not found" despite good data existing.
    validation_session_uuid = uuid.UUID(VALIDATION_SESSION_ID)
    validation_stints = stints[stints["session_id"] == validation_session_uuid]
    driver_ids_by_code: dict[str, Any] = {}
    driver_codes = await fetch_driver_codes(set(validation_stints["driver_id"]))
    for driver_id, code in driver_codes.items():
        driver_ids_by_code[code] = driver_id

    for code in VALIDATION_DRIVER_CODES:
        driver_id = driver_ids_by_code.get(code)
        if driver_id is None:
            logger.warning("%s: not found in validation session's stints", code)
            continue
        driver_stints = validation_stints[validation_stints["driver_id"] == driver_id].sort_values(
            "stint_number"
        )
        if len(driver_stints) < 2:
            logger.warning("%s: fewer than 2 stints, no pit event to validate", code)
            continue
        out_lap = int(driver_stints.iloc[1]["start_lap"])
        pit_lap = out_lap - 1

        driver_frame = pit_trajectory[
            (pit_trajectory["session_id"] == validation_session_uuid)
            & (pit_trajectory["driver_id"] == driver_id)
        ]
        print_trajectory(code, driver_frame, pit_lap, out_lap, old_model, new_model)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-training",
        action="store_true",
        help="Only fetch/report label positive rates, skip the actual model training.",
    )
    args = parser.parse_args()
    asyncio.run(run(skip_training=args.skip_training))


if __name__ == "__main__":
    main()
