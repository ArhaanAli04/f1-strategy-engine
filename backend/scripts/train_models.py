"""Train all 7 F1 strategy ML models on 2018-2024 data and upload to S3.

Trains 5 tire degradation regressors (one per compound), the pit predictor
classifier, and the safety car Poisson model. Each is evaluated against the
2025 holdout season and only promoted to the 'production' S3 tag if its
holdout MAE improves on the current production model's holdout MAE (first
run always promotes, since there is no existing production model to beat).

track_temp/air_temp are fetched here (and still stored via
tire_deg_model.add_engineered_features) but are not part of
tire_deg_model.FEATURE_COLUMNS as of 2026-07-16 — a weather-aware retrain
regressed holdout MAE 30-40% and the promotion guard correctly refused to
replace production models, so training here intentionally matches the
6-feature schema actually deployed. See tire_deg_model.py's module
docstring and CLAUDE.md's Data Quality Notes.

If a tire_deg compound has no holdout-season data (e.g. a dry 2025 means zero
WET laps), promotion falls back to comparing cv_mae instead of a true holdout
score — see promotion_basis in that model's metrics.json.

pit_predictor is trained on laps _fetch_laps() returns regardless of is_valid,
since the pit/in/out laps FastF1 marks invalid are exactly its positive-class
target. tire_deg_model and safety_car_model still filter to is_valid laps only.

Run via: make train
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import joblib
import numpy as np
import pandas as pd
from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_aws_settings
from backend.core.database import get_engine
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData, TireStint
from backend.services.ml import pit_predictor, safety_car_model, tire_deg_model

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

TRAIN_SEASON_START = 2018
TRAIN_SEASON_END = 2024
HOLDOUT_SEASON = 2025

COMPOUND_TO_FILENAME = {
    "SOFT": "tire_deg_soft.pkl",
    "MEDIUM": "tire_deg_medium.pkl",
    "HARD": "tire_deg_hard.pkl",
    "INTERMEDIATE": "tire_deg_inter.pkl",
    "WET": "tire_deg_wet.pkl",
}

MODEL_DIR = Path("models")


async def _fetch_laps() -> pd.DataFrame:
    """Fetch all timed laps for 2018-2025 with circuit/season context, including invalid ones.

    is_valid is included (not filtered) because pit_predictor's positive class is
    exactly the pit/in/out laps FastF1 marks invalid. Callers that need pace-only
    data (tire_deg_model, safety_car_model) must filter on is_valid themselves.

    Args: None.
    Returns: One row per lap_data row in [TRAIN_SEASON_START, HOLDOUT_SEASON].
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
            Race.season.between(TRAIN_SEASON_START, HOLDOUT_SEASON),
            LapData.lap_time_seconds.is_not(None),
        )
    )
    async with session_factory() as db:
        result = await db.execute(query)
        rows = result.all()

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


async def _fetch_stints() -> pd.DataFrame:
    """Fetch tire_stints rows needed to label pit laps.

    Args: None.
    Returns: One row per tire_stints row in [TRAIN_SEASON_START, HOLDOUT_SEASON].
    """
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
        .where(Race.season.between(TRAIN_SEASON_START, HOLDOUT_SEASON))
    )
    async with session_factory() as db:
        result = await db.execute(query)
        rows = result.all()

    return pd.DataFrame(rows, columns=["session_id", "driver_id", "stint_number", "start_lap"])


def _encode_categoricals(laps: pd.DataFrame) -> pd.DataFrame:
    """Add circuit/driver/compound integer codes, fit across the full 2018-2025 set.

    Encoding across the combined set (rather than fitting on train and applying to
    holdout) avoids unseen-category failures for drivers debuting in 2025 — this is
    an ID mapping, not a target-derived statistic, so it introduces no leakage.

    Args:
        laps: Raw laps frame with circuit_name, driver_id, compound columns.
    Returns:
        Copy of laps with circuit_id_encoded, driver_id_encoded, compound_encoded added.
    """
    df = laps.copy()
    df["circuit_id_encoded"] = pd.Categorical(df["circuit_name"]).codes
    df["driver_id_encoded"] = pd.Categorical(df["driver_id"].astype(str)).codes
    df["compound_encoded"] = pd.Categorical(df["compound"]).codes
    return df


def _split(laps: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = laps[laps["season"] <= TRAIN_SEASON_END].copy()
    holdout = laps[laps["season"] == HOLDOUT_SEASON].copy()
    return train, holdout


def _s3_client() -> Any:
    settings = get_aws_settings()
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )


def _download_metrics(
    client: Any, bucket: str, tag: str, filename: str
) -> dict[str, float | str] | None:
    try:
        obj = client.get_object(Bucket=bucket, Key=f"{tag}/{filename}.metrics.json")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise
    return dict(json.loads(obj["Body"].read()))


def _upload(
    client: Any,
    bucket: str,
    tag: str,
    filename: str,
    local_path: Path,
    metrics: dict[str, float | str],
) -> None:
    client.upload_file(str(local_path), bucket, f"{tag}/{filename}")
    client.put_object(
        Bucket=bucket,
        Key=f"{tag}/{filename}.metrics.json",
        Body=json.dumps(metrics).encode("utf-8"),
    )


def _serialize_evaluate_and_upload(
    client: Any,
    bucket: str,
    version_tag: str,
    filename: str,
    model_obj: Any,
    metrics: dict[str, float | str],
) -> bool:
    """Serialize a model, upload it under version_tag, and promote if holdout MAE improved.

    Args:
        client: boto3 S3 client.
        bucket: S3 bucket name.
        version_tag: This run's version tag (YYYYMMDD-HHMMSS).
        filename: Model registry filename, e.g. "tire_deg_soft.pkl".
        model_obj: The fitted model/pipeline to serialize with joblib.
        metrics: Metrics dict; must include "holdout_mae" for promotion comparison.
    Returns:
        True if this run's model was promoted to the 'production' tag.
    """
    local_path = MODEL_DIR / filename
    joblib.dump(model_obj, local_path)

    _upload(client, bucket, version_tag, filename, local_path, metrics)

    current_production = _download_metrics(client, bucket, "production", filename)
    current_holdout_mae = (
        float(current_production["holdout_mae"]) if current_production is not None else None
    )
    holdout_mae = float(metrics["holdout_mae"])
    should_promote = current_holdout_mae is None or holdout_mae < current_holdout_mae
    if should_promote:
        _upload(client, bucket, "production", filename, local_path, metrics)

    logger.info(
        "%s: holdout_mae=%.5f promoted=%s basis=%s (previous production holdout_mae=%s)",
        filename,
        holdout_mae,
        should_promote,
        metrics.get("promotion_basis", "holdout"),
        f"{current_holdout_mae:.5f}" if current_holdout_mae is not None else "none",
    )
    return should_promote


def _add_predicted_life_remaining(
    df: pd.DataFrame, tire_deg_results: dict[str, tire_deg_model.TireDegTrainResult]
) -> pd.Series:
    """Estimate predicted_life_remaining per row using each row's compound-specific model.

    Args:
        df: Must include compound, lap_number, compound_encoded, tyre_age_laps,
            fuel_adjusted_time, circuit_id_encoded, driver_id_encoded.
        tire_deg_results: Fitted tire degradation results, keyed by compound.
    Returns:
        Series aligned to df.index with the estimated laps remaining.
    """
    out = pd.Series(
        tire_deg_model.MAX_LOOKAHEAD_LAPS,
        index=df.index,
        dtype=np.int64,
        name="predicted_life_remaining",
    )
    for compound, group in df.groupby("compound"):
        result = tire_deg_results.get(compound)
        if result is None:
            continue
        life = tire_deg_model.predict_life_remaining_batch(
            result.pipeline,
            group["lap_number"].to_numpy(),
            group["compound_encoded"].to_numpy(),
            group["tyre_age_laps"].to_numpy(),
            group["fuel_adjusted_time"].to_numpy(),
            group["circuit_id_encoded"].to_numpy(),
            group["driver_id_encoded"].to_numpy(),
        )
        out.loc[group.index] = life
    return out


def _add_safety_car_probability(
    df: pd.DataFrame, sc_model: safety_car_model.SafetyCarModel
) -> pd.Series:
    """Vectorized P(SC/VSC in next 1 lap) for every row, from the fitted rate model.

    Args:
        df: Must include circuit_name, lap_number, compound.
        sc_model: Fitted SafetyCarModel.
    Returns:
        Series aligned to df.index with the probability.
    """
    base = df["circuit_name"].map(sc_model.circuit_rates).fillna(sc_model.default_rate).to_numpy()
    lap1_mult = np.where(df["lap_number"].to_numpy() == 1, safety_car_model.LAP1_MULTIPLIER, 1.0)
    wet_mult = np.where(
        df["compound"].isin(safety_car_model.WET_COMPOUNDS).to_numpy(),
        safety_car_model.WET_MULTIPLIER,
        1.0,
    )
    street_mult = np.where(
        df["circuit_name"].isin(safety_car_model.STREET_CIRCUITS).to_numpy(),
        safety_car_model.STREET_MULTIPLIER,
        1.0,
    )
    lam = base * lap1_mult * wet_mult * street_mult
    return pd.Series(1.0 - np.exp(-lam), index=df.index, name="safety_car_probability")


async def train_all() -> None:
    MODEL_DIR.mkdir(exist_ok=True)

    logger.info("Fetching laps and stints (%d-%d)...", TRAIN_SEASON_START, HOLDOUT_SEASON)
    raw_laps = await _fetch_laps()
    stints = await _fetch_stints()
    await get_engine().dispose()

    # Pace-based models (tire_deg, safety_car) only want is_valid laps.
    laps = raw_laps[raw_laps["is_valid"]].drop(columns=["is_valid"]).copy()
    laps["laps_in_session"] = laps.groupby("session_id")["lap_number"].transform("max")
    laps = _encode_categoricals(laps)
    train_laps, holdout_laps = _split(laps)
    logger.info("Train laps: %d, holdout laps: %d", len(train_laps), len(holdout_laps))

    # pit_predictor needs the pit/in/out laps is_valid excludes — they're its label.
    pit_laps = raw_laps.drop(columns=["is_valid"]).copy()
    pit_laps["laps_in_session"] = pit_laps.groupby("session_id")["lap_number"].transform("max")
    pit_laps = _encode_categoricals(pit_laps)
    pit_train_laps, pit_holdout_laps = _split(pit_laps)

    client = _s3_client()
    bucket = get_aws_settings().aws_bucket_name
    version_tag = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    # --- Tire degradation models (5x) ---
    tire_deg_results: dict[str, tire_deg_model.TireDegTrainResult] = {}
    for compound, filename in COMPOUND_TO_FILENAME.items():
        c_train = tire_deg_model.add_engineered_features(
            train_laps[train_laps["compound"] == compound]
        )
        c_holdout = tire_deg_model.add_engineered_features(
            holdout_laps[holdout_laps["compound"] == compound]
        )
        if c_train.empty:
            logger.warning("Skipping %s: no training data for %s", filename, compound)
            continue

        result = tire_deg_model.train_tire_degradation_model(c_train, compound)

        if c_holdout.empty:
            # No holdout-season data for this compound (e.g. a dry 2025 means
            # zero WET laps) — fall back to comparing cv_mae instead of a true
            # holdout score, since that's the closest reference we have.
            holdout_mae = result.cv_mae
            promotion_basis = "cv_only"
            logger.warning(
                "%s: no %s holdout data in season %d, falling back to CV-only "
                "promotion (cv_mae=%.5f is not a true holdout score)",
                filename,
                compound,
                HOLDOUT_SEASON,
                result.cv_mae,
            )
        else:
            holdout_mae = tire_deg_model.evaluate_holdout(result.pipeline, c_holdout)
            promotion_basis = "holdout"

        _serialize_evaluate_and_upload(
            client,
            bucket,
            version_tag,
            filename,
            result.pipeline,
            {
                "cv_mae": result.cv_mae,
                "cv_rmse": result.cv_rmse,
                "holdout_mae": holdout_mae,
                "n_samples": result.n_samples,
                "promotion_basis": promotion_basis,
            },
        )
        tire_deg_results[compound] = result

    # --- Safety car model ---
    sc_train = safety_car_model.build_lap_flags(train_laps)
    sc_holdout = safety_car_model.build_lap_flags(holdout_laps)
    sc_model = safety_car_model.train_safety_car_model(sc_train)
    sc_holdout_mae = safety_car_model.evaluate_holdout(sc_model, sc_holdout)
    _serialize_evaluate_and_upload(
        client,
        bucket,
        version_tag,
        "safety_car_model.pkl",
        sc_model,
        {"holdout_mae": sc_holdout_mae, "n_circuits": len(sc_model.circuit_rates)},
    )

    # --- Pit predictor (depends on tire_deg_results + sc_model) ---
    pit_train = pit_predictor.prepare_pit_predictor_features(pit_train_laps, stints)
    pit_holdout = pit_predictor.prepare_pit_predictor_features(pit_holdout_laps, stints)

    pit_train = tire_deg_model.add_engineered_features(pit_train)
    pit_holdout = tire_deg_model.add_engineered_features(pit_holdout)

    pit_train["predicted_life_remaining"] = _add_predicted_life_remaining(
        pit_train, tire_deg_results
    )
    pit_holdout["predicted_life_remaining"] = _add_predicted_life_remaining(
        pit_holdout, tire_deg_results
    )
    pit_train["safety_car_probability"] = _add_safety_car_probability(pit_train, sc_model)
    pit_holdout["safety_car_probability"] = _add_safety_car_probability(pit_holdout, sc_model)

    pit_result = pit_predictor.train_pit_predictor(pit_train)
    pit_holdout_mae = pit_predictor.evaluate_holdout(pit_result.model, pit_holdout)
    _serialize_evaluate_and_upload(
        client,
        bucket,
        version_tag,
        "pit_predictor.pkl",
        pit_result.model,
        {
            "cv_auc": pit_result.cv_auc,
            "holdout_mae": pit_holdout_mae,
            "positive_rate": pit_result.positive_rate,
            "n_samples": pit_result.n_samples,
        },
    )

    logger.info("Training complete. version_tag=%s", version_tag)


def main() -> None:
    asyncio.run(train_all())


if __name__ == "__main__":
    main()
