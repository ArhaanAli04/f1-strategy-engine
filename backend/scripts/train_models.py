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

Each tire_deg model's metrics.json also carries driver_id_to_code/
circuit_name_to_code (tire_deg_model.build_categorical_encoding_maps) — the
real pd.Categorical code map encode_categoricals fit for this run, embedded
per-model rather than as one shared artifact since each compound is promoted
independently (see tire_deg_model.py's own "Training-time categorical
encoding" section for why this exists and CLAUDE.md's Deferred Wiring entry
for the measured cost of the crc32 stand-in it replaces at inference).

pit_predictor is trained on laps fetch_laps_from_db() returns regardless of is_valid,
since the pit/in/out laps FastF1 marks invalid are exactly its positive-class
target. tire_deg_model and safety_car_model still filter to is_valid laps only.

Run via: make train

encode_categoricals, split_train_holdout, s3_client, download_metrics, upload_model,
serialize_evaluate_and_upload, add_predicted_life_remaining, and add_safety_car_probability
are public (Day 21) so retrain_incremental.py can reuse the same encode/train/evaluate/
promote logic against a differently-sourced DataFrame (S3 parquet + live FastF1 fetch,
no DB) instead of duplicating it. fetch_laps_from_db/fetch_stints_from_db stay DB-specific
and are only reused by export_training_data.py, which populates that parquet cache.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
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


async def fetch_laps_from_db() -> pd.DataFrame:
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


async def fetch_stints_from_db() -> pd.DataFrame:
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


def encode_categoricals(laps: pd.DataFrame) -> pd.DataFrame:
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


def split_train_holdout(
    laps: pd.DataFrame,
    train_seasons: set[int] | None = None,
    holdout_season: int = HOLDOUT_SEASON,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split laps into a train set (explicit season list) and a fixed holdout season.

    Args:
        laps: Combined laps frame with a "season" column.
        train_seasons: Seasons to include in training. Defaults to
            {TRAIN_SEASON_START..TRAIN_SEASON_END} (the historical base range) when
            not given — this preserves train_all()'s original behavior. Callers doing
            incremental retraining (e.g. retrain_incremental.py) pass an explicit set
            that also includes the current season's completed rounds.
        holdout_season: Season held out for MAE comparison. Defaults to HOLDOUT_SEASON
            so incremental retraining stays comparable against the same benchmark the
            base production models were evaluated against.
    Returns:
        (train, holdout) DataFrames.
    """
    if train_seasons is None:
        train_seasons = set(range(TRAIN_SEASON_START, TRAIN_SEASON_END + 1))
    train = laps[laps["season"].isin(train_seasons)].copy()
    holdout = laps[laps["season"] == holdout_season].copy()
    return train, holdout


def s3_client() -> Any:
    settings = get_aws_settings()
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )


def fitted_feature_count(model_obj: Any) -> int | None:
    """Fitted input feature count for any model object in the training registry.

    Handles every model shape serialize_evaluate_and_upload sees: a tire_deg
    Pipeline (StandardScaler -> XGBRegressor; delegates to tire_deg_model.
    pipeline_feature_count), a bare pit_predictor LGBMClassifier (reads
    n_features_in_ directly, since it has no named_steps), and
    safety_car_model.SafetyCarModel (a plain per-circuit-rate dataclass with no
    feature vector at all — correctly returns None here, not a mismatch:
    schema compatibility has no meaning for a model with no feature vector).

    Args:
        model_obj: A fitted model/pipeline from the training registry, or any
            other object (defensive — a registry entry could in principle be
            missing/wrong-typed).
    Returns:
        The input feature count as an int, or None if it can't be determined
        (no feature-vector concept applies, or the object is unfitted/unknown)
        — callers must treat None as "not applicable", not as a mismatch.
    """
    count = tire_deg_model.pipeline_feature_count(model_obj)
    if count is not None:
        return count
    try:
        n_features = model_obj.n_features_in_
    except AttributeError:
        return None
    return int(n_features) if isinstance(n_features, int | np.integer) else None


def download_metrics(client: Any, bucket: str, tag: str, filename: str) -> dict[str, Any] | None:
    try:
        obj = client.get_object(Bucket=bucket, Key=f"{tag}/{filename}.metrics.json")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise
    return dict(json.loads(obj["Body"].read()))


def upload_model(
    client: Any,
    bucket: str,
    tag: str,
    filename: str,
    local_path: Path,
    metrics: dict[str, Any],
) -> None:
    client.upload_file(str(local_path), bucket, f"{tag}/{filename}")
    client.put_object(
        Bucket=bucket,
        Key=f"{tag}/{filename}.metrics.json",
        Body=json.dumps(metrics).encode("utf-8"),
    )


@dataclass(frozen=True)
class PromotionOutcome:
    """Result of a promotion decision.

    reason is one of:
        "no_production_model"      — first run for this filename, nothing to compare against.
        "schema_mismatch"          — the production incumbent's feature COUNT didn't match
                                      this candidate's; promoted regardless of holdout_mae,
                                      since an incompatible incumbent cannot even serve
                                      inference (see tire_deg_wet.pkl's 8-vs-6-feature crash).
        "training_schema_version_mismatch" — the incumbent's recorded
                                      training_schema_version (or its absence) didn't match
                                      this candidate's declared version — the candidate's
                                      FEATURE/TARGET definitions changed in a way that makes
                                      holdout_mae non-comparable across versions, even though
                                      feature count and names may be identical (see
                                      pit_predictor.TRAINING_SCHEMA_VERSION's label-definition
                                      case). Promoted regardless of holdout_mae, same rationale
                                      as schema_mismatch — the two models aren't measuring the
                                      same thing, so "improved" has no meaning across them.
        "feature_names_mismatch"   — the incumbent's recorded feature_names didn't match this
                                      candidate's, despite an identical feature COUNT (e.g.
                                      CP1's fuel_adjusted_time -> fuel_load_penalty rename) —
                                      a softer failure mode than schema_mismatch (no shape
                                      error at inference, just silently wrong predictions), so
                                      it needs its own check rather than relying on a crash to
                                      ever surface it. Promoted regardless of holdout_mae.
        "holdout_mae_improved"     — schema, names, and version all matched (or weren't
                                      applicable); candidate's holdout_mae beat the incumbent's.
        "holdout_mae_not_improved" — schema, names, and version all matched (or weren't
                                      applicable); candidate's holdout_mae did not beat the
                                      incumbent's.
    """

    promoted: bool
    reason: str


def _resolve_incumbent_schema(
    client: Any, bucket: str, filename: str, current_metrics: dict[str, Any]
) -> tuple[int | None, bool]:
    """Determine the production incumbent's feature count, backfilling its sidecar if needed.

    Prefers the sidecar's own recorded n_features — present on every sidecar this
    module writes once this schema check is live. Falls back to downloading and
    introspecting the production .pkl only when the sidecar predates this fix and
    has no schema recorded; on a successful introspection, backfills the recovered
    count into the sidecar in place (existing metric values preserved, n_features/
    schema_source="introspected" added, feature_names left absent since names
    aren't recoverable from a fitted object) — so this filename's incumbent never
    needs a .pkl download again after the first time.

    Args:
        client: boto3 S3 client.
        bucket: S3 bucket name.
        filename: Model registry filename, e.g. "tire_deg_wet.pkl".
        current_metrics: The production sidecar's already-downloaded metrics dict
            (caller must have already confirmed a production model exists).
    Returns:
        (n_features, unrecoverable). unrecoverable is True only when the .pkl
        itself could not be downloaded, loaded, or yielded a determinable feature
        count — the caller treats an unrecoverable incumbent as incompatible (a
        production model that can't even be loaded is exactly what should be
        replaced), not as "unknown, don't block."
    """
    recorded = current_metrics.get("n_features")
    if recorded is not None:
        return int(recorded), False

    local_path = MODEL_DIR / f"_incumbent_{filename}"
    try:
        client.download_file(bucket, f"production/{filename}", str(local_path))
        incumbent_obj = joblib.load(local_path)
    except Exception:
        logger.warning(
            "%s: production .pkl could not be downloaded or loaded to recover its "
            "feature schema (legacy sidecar, no n_features recorded) — treating the "
            "incumbent as incompatible",
            filename,
            exc_info=True,
        )
        return None, True
    finally:
        local_path.unlink(missing_ok=True)

    n_features = fitted_feature_count(incumbent_obj)
    if n_features is None:
        logger.warning(
            "%s: production .pkl loaded but has no determinable feature count — "
            "treating the incumbent's schema as unrecoverable",
            filename,
        )
        return None, True

    backfilled = dict(current_metrics)
    backfilled["n_features"] = n_features
    backfilled["schema_source"] = "introspected"
    client.put_object(
        Bucket=bucket,
        Key=f"production/{filename}.metrics.json",
        Body=json.dumps(backfilled).encode("utf-8"),
    )
    logger.info(
        "%s: recovered production incumbent's feature schema via .pkl introspection "
        "(n_features=%d) and backfilled its sidecar — future runs won't need to "
        "download this .pkl again",
        filename,
        n_features,
    )
    return n_features, False


def _feature_names_mismatch(
    candidate_feature_names: list[str] | None, current_metrics: dict[str, Any]
) -> bool:
    """Whether the incumbent's recorded feature_names differ from the candidate's.

    A softer companion to _resolve_incumbent_schema's feature-COUNT check: two
    models can share a feature count while a feature at the same position means
    something completely different (CP1's fuel_adjusted_time -> fuel_load_penalty
    rename — same 6 features, same position, incompatible values). That class of
    drift raises no shape error at inference, it just silently predicts against
    the wrong distribution — so unlike a count mismatch, nothing will ever force
    this to surface on its own; it needs this explicit check.

    Args:
        candidate_feature_names: This candidate's feature_names, or None for a
            model type with no feature-vector concept — always returns False.
        current_metrics: The production sidecar's already-downloaded metrics dict.
    Returns:
        True if the candidate must be force-promoted regardless of holdout_mae.
        A legacy incumbent with no feature_names recorded at all is treated as
        mismatched — same "unrecoverable is incompatible" stance
        _resolve_incumbent_schema takes for feature count, and unlike count there
        is no .pkl to introspect a name list back out of, so this can never be
        recovered after the fact the way a legacy count can.
    """
    if candidate_feature_names is None:
        return False
    return current_metrics.get("feature_names") != candidate_feature_names


def _training_schema_mismatch(
    candidate_training_schema_version: int | None, current_metrics: dict[str, Any]
) -> bool:
    """Whether the incumbent's recorded training_schema_version differs from the candidate's.

    Catches a class of drift neither the feature-count nor feature-names check
    can: pit_predictor's 2026-09-04 label fix (see pit_predictor.
    TRAINING_SCHEMA_VERSION) changed what TARGET_COLUMN's values mean while
    FEATURE_COLUMNS stayed byte-for-byte identical — there is no feature-side
    signal of any kind to compare, so only an explicit version bump can flag the
    two models as measuring different things.

    Args:
        candidate_training_schema_version: This candidate's declared version
            (tire_deg_model.TRAINING_SCHEMA_VERSION / pit_predictor.
            TRAINING_SCHEMA_VERSION), or None for a model type that doesn't use
            this concept (safety_car_model) — always returns False.
        current_metrics: The production sidecar's already-downloaded metrics dict.
    Returns:
        True if the candidate must be force-promoted regardless of holdout_mae.
        Like feature_names, a training_schema_version has no way to be recovered
        from a fitted model object — it is pure training-time metadata — so an
        incumbent sidecar that never recorded one (every real sidecar as of
        2026-09-09) is always treated as mismatched whenever the candidate
        declares a version.
    """
    if candidate_training_schema_version is None:
        return False
    return current_metrics.get("training_schema_version") != candidate_training_schema_version


def serialize_evaluate_and_upload(
    client: Any,
    bucket: str,
    version_tag: str,
    filename: str,
    model_obj: Any,
    metrics: dict[str, Any],
    feature_names: list[str] | None = None,
    training_schema_version: int | None = None,
) -> PromotionOutcome:
    """Serialize a model, upload it under version_tag, and promote based on schema + holdout MAE.

    Promotion logic, in order:
    1. No existing production model for this filename -> promote (first run).
    2. The production incumbent is INCOMPATIBLE with this candidate in any of three
       independent ways -> force-promote regardless of holdout_mae, since an
       incompatible incumbent's holdout_mae isn't measuring the same thing this
       candidate's is (see each check's own docstring for what it catches and why
       the other two can't):
         a. Feature COUNT differs (fitted_feature_count(model_obj) is not None and
            the incumbent's is unrecoverable or different) — a hard crash risk,
            the original form of this check (tire_deg_wet.pkl's 8-vs-6-feature
            crash).
         b. Feature NAMES differ despite an identical count (_feature_names_mismatch).
         c. training_schema_version differs (_training_schema_mismatch) — catches a
            TARGET-only definition change with no feature-side signal at all (e.g.
            pit_predictor's label fix).
    3. Otherwise (fully compatible, or no incumbent to compare against) -> promote
       only if holdout_mae improved, same as before any of this existed.

    Every sidecar this function writes (the version_tag copy always, the production
    copy on promotion) carries n_features/feature_names/schema_source="declared"/
    training_schema_version, so a legacy incumbent's feature COUNT only ever needs
    recovering via .pkl download once — see _resolve_incumbent_schema. feature_names
    and training_schema_version have no equivalent recovery path (see their own
    docstrings) — an incumbent sidecar predating this function's declaration of
    either is permanently treated as mismatched on that axis, which is the correct,
    conservative default: force one real promotion under this fix and the sidecar
    carries both fields from then on.

    Args:
        client: boto3 S3 client.
        bucket: S3 bucket name.
        version_tag: This run's version tag (YYYYMMDD-HHMMSS).
        filename: Model registry filename, e.g. "tire_deg_soft.pkl".
        model_obj: The fitted model/pipeline to serialize with joblib.
        metrics: Metrics dict; must include "holdout_mae" for promotion comparison.
        feature_names: This model's FEATURE_COLUMNS, in training order, or None for
            a model type with no feature-vector concept (safety_car_model). When
            given, must have exactly fitted_feature_count(model_obj) entries.
        training_schema_version: This model's TRAINING_SCHEMA_VERSION (tire_deg_model
            / pit_predictor), or None for a model type that doesn't declare one
            (safety_car_model) — see _training_schema_mismatch.
    Returns:
        PromotionOutcome — whether this run's model was promoted to the
        'production' tag, and why.
    """
    candidate_n_features = fitted_feature_count(model_obj)
    if (
        feature_names is not None
        and candidate_n_features is not None
        and len(feature_names) != candidate_n_features
    ):
        raise ValueError(
            f"{filename}: feature_names has {len(feature_names)} entries but the "
            f"fitted model expects {candidate_n_features} — caller bug, refusing to "
            "upload a candidate with an inconsistent schema declaration"
        )

    schema_metrics = dict(metrics)
    schema_metrics["n_features"] = candidate_n_features
    schema_metrics["feature_names"] = feature_names
    schema_metrics["schema_source"] = "declared"
    schema_metrics["training_schema_version"] = training_schema_version

    local_path = MODEL_DIR / filename
    joblib.dump(model_obj, local_path)

    upload_model(client, bucket, version_tag, filename, local_path, schema_metrics)

    current_production = download_metrics(client, bucket, "production", filename)
    current_holdout_mae = (
        float(current_production["holdout_mae"]) if current_production is not None else None
    )
    holdout_mae = float(metrics["holdout_mae"])

    if current_production is None:
        should_promote = True
        reason = "no_production_model"
    else:
        incumbent_n_features: int | None = None
        incumbent_unrecoverable = False
        if candidate_n_features is not None:
            incumbent_n_features, incumbent_unrecoverable = _resolve_incumbent_schema(
                client, bucket, filename, current_production
            )
        feature_count_mismatch = candidate_n_features is not None and (
            incumbent_unrecoverable or incumbent_n_features != candidate_n_features
        )
        names_mismatch = _feature_names_mismatch(feature_names, current_production)
        version_mismatch = _training_schema_mismatch(training_schema_version, current_production)

        if feature_count_mismatch or names_mismatch or version_mismatch:
            should_promote = True
            # Priority when more than one fires at once (e.g. CP1's tire_deg
            # rename tripped both names_mismatch and version_mismatch): report
            # the highest-severity check that actually caused promotion —
            # feature_count_mismatch (a crash risk) first, then the deliberate
            # version signal, then the automatic name-based safety net.
            reason = (
                "schema_mismatch"
                if feature_count_mismatch
                else "training_schema_version_mismatch"
                if version_mismatch
                else "feature_names_mismatch"
            )
            logger.warning(
                "%s: production incumbent is incompatible with this candidate — "
                "force-promoting regardless of holdout_mae (candidate=%.5f, "
                "incumbent=%s). feature_count_mismatch=%s (incumbent_n_features=%s, "
                "candidate_n_features=%s%s) feature_names_mismatch=%s "
                "training_schema_version_mismatch=%s (incumbent_version=%s, "
                "candidate_version=%s)",
                filename,
                holdout_mae,
                f"{current_holdout_mae:.5f}" if current_holdout_mae is not None else "none",
                feature_count_mismatch,
                incumbent_n_features,
                candidate_n_features,
                " [incumbent .pkl unrecoverable]" if incumbent_unrecoverable else "",
                names_mismatch,
                version_mismatch,
                current_production.get("training_schema_version"),
                training_schema_version,
            )
        else:
            # Read straight off current_production (not the separately-derived
            # current_holdout_mae) so mypy's non-None narrowing of this branch's
            # own if-condition variable applies directly, without a bare assert.
            should_promote = holdout_mae < float(current_production["holdout_mae"])
            reason = "holdout_mae_improved" if should_promote else "holdout_mae_not_improved"

    if should_promote:
        upload_model(client, bucket, "production", filename, local_path, schema_metrics)

    logger.info(
        "%s: holdout_mae=%.5f promoted=%s reason=%s basis=%s (previous production holdout_mae=%s)",
        filename,
        holdout_mae,
        should_promote,
        reason,
        metrics.get("promotion_basis", "holdout"),
        f"{current_holdout_mae:.5f}" if current_holdout_mae is not None else "none",
    )
    return PromotionOutcome(promoted=should_promote, reason=reason)


def add_predicted_life_remaining(
    df: pd.DataFrame, tire_deg_results: dict[str, tire_deg_model.TireDegTrainResult]
) -> pd.Series:
    """Estimate predicted_life_remaining per row using each row's compound-specific model.

    Args:
        df: Must include compound, lap_number, compound_encoded, tyre_age_laps,
            fuel_load_penalty, circuit_id_encoded, driver_id_encoded.
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
            group["fuel_load_penalty"].to_numpy(),
            group["circuit_id_encoded"].to_numpy(),
            group["driver_id_encoded"].to_numpy(),
        )
        out.loc[group.index] = life
    return out


def add_safety_car_probability(
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
    raw_laps = await fetch_laps_from_db()
    stints = await fetch_stints_from_db()
    await get_engine().dispose()

    # Pace-based models (tire_deg, safety_car) only want is_valid laps.
    laps = raw_laps[raw_laps["is_valid"]].drop(columns=["is_valid"]).copy()
    laps["laps_in_session"] = laps.groupby("session_id")["lap_number"].transform("max")
    laps = encode_categoricals(laps)
    train_laps, holdout_laps = split_train_holdout(laps)
    logger.info("Train laps: %d, holdout laps: %d", len(train_laps), len(holdout_laps))

    # Recovers the driver_id/circuit_name -> pd.Categorical code map encode_categoricals
    # just fit, so inference can use the model's real training-time codes instead of the
    # crc32 stand-in — see tire_deg_model.py's "Training-time categorical encoding"
    # section. One shared map for this run: every tire_deg compound below is trained
    # from this same encode_categoricals(laps) call, just filtered by compound after.
    encoding_maps = tire_deg_model.build_categorical_encoding_maps(laps)

    # pit_predictor needs the pit/in/out laps is_valid excludes — they're its label.
    pit_laps = raw_laps.drop(columns=["is_valid"]).copy()
    pit_laps["laps_in_session"] = pit_laps.groupby("session_id")["lap_number"].transform("max")
    pit_laps = encode_categoricals(pit_laps)
    pit_train_laps, pit_holdout_laps = split_train_holdout(pit_laps)

    client = s3_client()
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

        serialize_evaluate_and_upload(
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
                **encoding_maps,
            },
            feature_names=tire_deg_model.FEATURE_COLUMNS,
            training_schema_version=tire_deg_model.TRAINING_SCHEMA_VERSION,
        )
        tire_deg_results[compound] = result

    # --- Safety car model ---
    # Unlike tire_deg (which wants is_valid=True only), this model must NOT be
    # fit on is_valid-filtered laps: a lap under an active SC/VSC period has an
    # anomalous lap time FastF1 marks is_valid=False, so filtering removes
    # virtually the entire positive-class signal (see safety_car_model.
    # TRAINING_SCHEMA_VERSION's docstring — this was the real, measured root
    # cause of production's all-zero circuit_rates). pit_train_laps/
    # pit_holdout_laps are already the unfiltered frame with the same
    # encode_categoricals/laps_in_session/split_train_holdout applied — reused
    # here rather than building a third copy.
    sc_train = safety_car_model.build_lap_flags(pit_train_laps)
    sc_holdout = safety_car_model.build_lap_flags(pit_holdout_laps)
    sc_model = safety_car_model.train_safety_car_model(sc_train)
    sc_holdout_mae = safety_car_model.evaluate_holdout(sc_model, sc_holdout)
    serialize_evaluate_and_upload(
        client,
        bucket,
        version_tag,
        "safety_car_model.pkl",
        sc_model,
        {"holdout_mae": sc_holdout_mae, "n_circuits": len(sc_model.circuit_rates)},
        training_schema_version=safety_car_model.TRAINING_SCHEMA_VERSION,
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
    serialize_evaluate_and_upload(
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
        feature_names=pit_predictor.FEATURE_COLUMNS,
        training_schema_version=pit_predictor.TRAINING_SCHEMA_VERSION,
    )

    logger.info("Training complete. version_tag=%s", version_tag)


def main() -> None:
    asyncio.run(train_all())


if __name__ == "__main__":
    main()
