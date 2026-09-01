"""Weekly incremental retraining entrypoint for train-models.yml (CI — no local
Postgres, no persistent FastF1 cache on the runner).

Downloads the static 2018-2025 base training corpus from S3 (written once,
manually, by export_training_data.py against a real Postgres) and adds the
current season's completed rounds on top of it, fetched directly from FastF1 —
no DB needed for that part, since only ingest_historical.py's Postgres *write*
ever required a database; the FastF1 *fetch* does not. Trains on
{TRAIN_SEASON_START..TRAIN_SEASON_END} U {CURRENT_SEASON's completed rounds},
holdout fixed at HOLDOUT_SEASON (the same benchmark the base production models
were evaluated against, so MAE comparisons stay apples-to-apples). Reuses
train_models.py's encode/train/evaluate/promote helpers rather than
duplicating them.

Every run re-fetches CURRENT_SEASON's completed rounds from FastF1 from
scratch — no cross-run state to track. A season has at most 24 rounds versus
the base corpus's ~168 (7 seasons x 24), so this stays cheap (~5-10 min) even
late in the season, unlike a full historical rebuild (~30-60 min).

Promotion is decided per-model (matching train_models.py's existing per-model
promotion guard) — a single weekly run can promote some models and leave
others as candidates; train-models.yml branches on each independently rather
than treating the run as one all-or-nothing outcome.

Writes retrain_summary.json (per-model holdout_mae / previous production
holdout_mae / promoted) for train-models.yml to branch on.

Run via: python -m backend.scripts.retrain_incremental
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fastf1._api
import fastf1.exceptions
import pandas as pd

from backend.core.config import get_aws_settings
from backend.scripts._ingest_common import _LOCATION_TO_CIRCUIT_NAME, RoundSkippedError, or_default
from backend.scripts.ingest_historical import lap_time_to_seconds, load_session
from backend.scripts.train_models import (
    COMPOUND_TO_FILENAME,
    HOLDOUT_SEASON,
    TRAIN_SEASON_END,
    TRAIN_SEASON_START,
    add_predicted_life_remaining,
    add_safety_car_probability,
    download_metrics,
    encode_categoricals,
    s3_client,
    serialize_evaluate_and_upload,
    split_train_holdout,
)
from backend.services.ml import pit_predictor, safety_car_model, tire_deg_model

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CURRENT_SEASON = 2026
BASE_S3_PREFIX = "training-data/base"
MODEL_DIR = Path("models")
CACHE_DIR = Path("training_data_cache")
SUMMARY_PATH = Path("retrain_summary.json")

LAP_COLUMNS = [
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
]
STINT_COLUMNS = ["session_id", "driver_id", "stint_number", "start_lap"]


def _download_base_corpus(client: Any, bucket: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Download the 2018-2025 base laps/stints parquet exported by export_training_data.py."""
    CACHE_DIR.mkdir(exist_ok=True)
    laps_path = CACHE_DIR / "laps.parquet"
    stints_path = CACHE_DIR / "stints.parquet"
    client.download_file(bucket, f"{BASE_S3_PREFIX}/laps.parquet", str(laps_path))
    client.download_file(bucket, f"{BASE_S3_PREFIX}/stints.parquet", str(stints_path))
    return pd.read_parquet(laps_path), pd.read_parquet(stints_path)


def _fetch_current_season_rounds() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch every completed round of CURRENT_SEASON directly from FastF1 (no DB).

    Reuses ingest_historical.py's load_session/RoundSkippedError resilience pattern —
    a round that errors (not yet run, or a transient fetch failure) is logged and
    skipped, matching ingest_historical.py's --all-rounds behavior, rather than
    stopping at the first failure.

    Returns:
        (laps, stints) DataFrames with the same columns fetch_laps_from_db()/
        fetch_stints_from_db() produce, so they concatenate cleanly with the base
        corpus. track_temp/air_temp are always None here — weather isn't part of
        tire_deg_model.FEATURE_COLUMNS (see CLAUDE.md Data Quality Notes), so this
        doesn't affect training.
    """
    lap_rows: list[dict[str, object]] = []
    stint_rows: list[dict[str, object]] = []

    for round_number in range(1, 25):
        try:
            session = load_session(CURRENT_SEASON, round_number, "R")
            laps = session.laps
        except (
            RoundSkippedError,
            fastf1.exceptions.DataNotLoadedError,
            fastf1._api.SessionNotAvailableError,
        ) as exc:
            logger.info("Skipping round %d: %s", round_number, exc)
            continue

        if laps.empty:
            logger.info("Skipping round %d: no lap data available", round_number)
            continue

        location = session.event["Location"]
        circuit_name = _LOCATION_TO_CIRCUIT_NAME.get(location)
        if circuit_name is None:
            logger.warning("No circuit mapping for '%s', skipping round %d", location, round_number)
            continue

        session_key = f"{CURRENT_SEASON}-R{round_number}-R"

        for _, lap in laps.iterrows():
            if pd.isna(lap["LapNumber"]) or pd.isna(lap["LapTime"]):
                continue
            lap_rows.append(
                {
                    "session_id": session_key,
                    "driver_id": lap["Driver"],
                    "lap_number": int(lap["LapNumber"]),
                    "lap_time_seconds": lap_time_to_seconds(lap["LapTime"]),
                    "compound": or_default(lap["Compound"], "UNKNOWN"),
                    "tyre_age_laps": (int(lap["TyreLife"]) if not pd.isna(lap["TyreLife"]) else 0),
                    "position": (int(lap["Position"]) if not pd.isna(lap["Position"]) else None),
                    "track_status": (
                        str(lap["TrackStatus"]) if not pd.isna(lap["TrackStatus"]) else None
                    ),
                    "track_temp": None,
                    "air_temp": None,
                    "is_valid": (
                        bool(lap["IsAccurate"]) if not pd.isna(lap["IsAccurate"]) else False
                    ),
                    "season": CURRENT_SEASON,
                    "circuit_name": circuit_name,
                }
            )

        grouped = laps.dropna(subset=["Stint"]).groupby(["Driver", "Stint"])
        for (driver_code, stint_number), stint_laps in grouped:
            stint_rows.append(
                {
                    "session_id": session_key,
                    "driver_id": driver_code,
                    "stint_number": int(stint_number),
                    "start_lap": int(stint_laps["LapNumber"].min()),
                }
            )

        logger.info("Fetched round %d (%s): %d lap row(s)", round_number, circuit_name, len(laps))

    return (
        pd.DataFrame(lap_rows, columns=LAP_COLUMNS),
        pd.DataFrame(stint_rows, columns=STINT_COLUMNS),
    )


def _promote_and_record(
    client: Any,
    bucket: str,
    version_tag: str,
    filename: str,
    model_obj: Any,
    metrics: dict[str, Any],
    summary: dict[str, dict[str, object]],
    feature_names: list[str] | None = None,
) -> None:
    previous = download_metrics(client, bucket, "production", filename)
    previous_mae = float(previous["holdout_mae"]) if previous is not None else None
    outcome = serialize_evaluate_and_upload(
        client, bucket, version_tag, filename, model_obj, metrics, feature_names=feature_names
    )
    summary[filename] = {
        "holdout_mae": float(metrics["holdout_mae"]),
        "previous_production_holdout_mae": previous_mae,
        "promoted": outcome.promoted,
        "promotion_reason": outcome.reason,
    }


def retrain() -> dict[str, dict[str, object]]:
    MODEL_DIR.mkdir(exist_ok=True)
    client = s3_client()
    bucket = get_aws_settings().aws_bucket_name

    logger.info(
        "Downloading base training corpus (%d-%d) from s3://%s/%s/...",
        TRAIN_SEASON_START,
        HOLDOUT_SEASON,
        bucket,
        BASE_S3_PREFIX,
    )
    base_laps, base_stints = _download_base_corpus(client, bucket)

    logger.info("Fetching season %d's completed rounds directly from FastF1...", CURRENT_SEASON)
    current_laps, current_stints = _fetch_current_season_rounds()
    logger.info("Fetched %d lap row(s) for season %d", len(current_laps), CURRENT_SEASON)

    raw_laps = pd.concat([base_laps, current_laps], ignore_index=True)
    stints = pd.concat([base_stints, current_stints], ignore_index=True)
    train_seasons = set(range(TRAIN_SEASON_START, TRAIN_SEASON_END + 1)) | {CURRENT_SEASON}

    laps = raw_laps[raw_laps["is_valid"]].drop(columns=["is_valid"]).copy()
    laps["laps_in_session"] = laps.groupby("session_id")["lap_number"].transform("max")
    laps = encode_categoricals(laps)
    train_laps, holdout_laps = split_train_holdout(laps, train_seasons=train_seasons)
    logger.info("Train laps: %d, holdout laps: %d", len(train_laps), len(holdout_laps))

    pit_laps = raw_laps.drop(columns=["is_valid"]).copy()
    pit_laps["laps_in_session"] = pit_laps.groupby("session_id")["lap_number"].transform("max")
    pit_laps = encode_categoricals(pit_laps)
    pit_train_laps, pit_holdout_laps = split_train_holdout(pit_laps, train_seasons=train_seasons)

    version_tag = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    summary: dict[str, dict[str, object]] = {}

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
            holdout_mae = result.cv_mae
            promotion_basis = "cv_only"
        else:
            holdout_mae = tire_deg_model.evaluate_holdout(result.pipeline, c_holdout)
            promotion_basis = "holdout"

        _promote_and_record(
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
            summary,
            feature_names=tire_deg_model.FEATURE_COLUMNS,
        )
        tire_deg_results[compound] = result

    # --- Safety car model ---
    sc_train = safety_car_model.build_lap_flags(train_laps)
    sc_holdout = safety_car_model.build_lap_flags(holdout_laps)
    sc_model = safety_car_model.train_safety_car_model(sc_train)
    sc_holdout_mae = safety_car_model.evaluate_holdout(sc_model, sc_holdout)
    _promote_and_record(
        client,
        bucket,
        version_tag,
        "safety_car_model.pkl",
        sc_model,
        {"holdout_mae": sc_holdout_mae, "n_circuits": len(sc_model.circuit_rates)},
        summary,
    )

    # --- Pit predictor (depends on tire_deg_results + sc_model) ---
    pit_train = pit_predictor.prepare_pit_predictor_features(pit_train_laps, stints)
    pit_holdout = pit_predictor.prepare_pit_predictor_features(pit_holdout_laps, stints)

    pit_train = tire_deg_model.add_engineered_features(pit_train)
    pit_holdout = tire_deg_model.add_engineered_features(pit_holdout)

    pit_train["predicted_life_remaining"] = add_predicted_life_remaining(
        pit_train, tire_deg_results
    )
    pit_holdout["predicted_life_remaining"] = add_predicted_life_remaining(
        pit_holdout, tire_deg_results
    )
    pit_train["safety_car_probability"] = add_safety_car_probability(pit_train, sc_model)
    pit_holdout["safety_car_probability"] = add_safety_car_probability(pit_holdout, sc_model)

    pit_result = pit_predictor.train_pit_predictor(pit_train)
    pit_holdout_mae = pit_predictor.evaluate_holdout(pit_result.model, pit_holdout)
    _promote_and_record(
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
        summary,
        feature_names=pit_predictor.FEATURE_COLUMNS,
    )

    logger.info("Incremental retraining complete. version_tag=%s", version_tag)
    return summary


def main() -> None:
    summary = retrain()
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2))
    logger.info("Wrote %s", SUMMARY_PATH)


if __name__ == "__main__":
    main()
