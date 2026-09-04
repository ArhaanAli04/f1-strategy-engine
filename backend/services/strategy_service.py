"""Pit window, undercut/overcut, and competitor-strategy predictions.

This module deliberately duplicates a small S3 model-loader and the
tire_deg_model/pit_predictor feature-encoding helpers rather than importing
prediction_worker.py or another services/ module — CLAUDE.md forbids
services/ importing other services/, and workers/ must not be imported by
services/ either (that's backwards layering). See pit_predictor.py's
docstring for the same convention already established for services/ml.

Several modelling gaps had to be worked around, all documented at the point
they're used rather than silently papered over:

- circuit_id_encoded/driver_id_encoded: train_models.py's encode_categoricals
  fits these via pd.Categorical(...).codes fresh per training run. Previously
  never persisted (prediction_worker.py had the same gap) — every inference
  call substituted a crc32 hash instead of the real training-time code,
  confirmed to inflate tire_deg holdout MAE by 50-265% depending on compound
  (scripts/evaluate_driver_features.py, see CLAUDE.md's Deferred Wiring
  entry). Fixed: each tire_deg model's own sidecar now carries its real
  driver_id/circuit_name -> code map (tire_deg_model.build_categorical_
  encoding_maps, embedded per-model since item 9 promotes each compound
  independently), loaded by _load_encoding_maps() and resolved via
  tire_deg_model.resolve_driver_code/resolve_circuit_code — every call site
  below picks the map matching whichever pipeline it's about to call, never
  one map applied across compounds. A missing map (legacy sidecar, or an id
  that debuted after a model's last training run) falls back to the same
  crc32 formula as before, per id — non-regressive by construction.
  compound_encoded uses a hardcoded alphabetical-order mapping instead, since
  {HARD, INTERMEDIATE, MEDIUM, SOFT, WET} is a small, fixed, near-certainly-
  fully-observed set — pd.Categorical's inferred code order for it is far
  more predictable than for circuit/driver IDs, and was never part of this
  gap.
- total_laps: neither Race nor Session persists race distance. It's
  approximated as MAX(lap_number) observed so far in the session, which
  under-estimates mid-race and converges to the true value near the finish.
- get_competitor_predicted_strategy holds gap_to_car_ahead/behind at
  pit_predictor.MAX_GAP_SECONDS and safety_car_probability at 0.0 — a real
  forward gap/SC model would need telemetry_service (forbidden import) or
  the full race_simulator.py multi-driver simulation, which is out of scope
  for a per-competitor pit-lap estimate.
- track_temp/air_temp: NOT part of the tire_deg feature vector as of
  2026-07-16 — adding them regressed holdout MAE 30-40% and the promotion
  guard correctly refused to replace production models (see CLAUDE.md Data
  Quality Notes), so the deployed "production" S3 models are still the
  pre-weather 6-feature versions. _resolve_weather() below is kept (prefers
  the live f1:{season}:{round}:weather:latest Redis key written by
  ingest_live_session.py, falling back to a DB circuit+compound average) but
  is currently unused by every function in this module — it's wired for
  when a weather-aware retrain gets promoted, not dead code to be deleted.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from pathlib import Path
from typing import Any

import boto3
import joblib
import numpy as np
import redis.asyncio as aioredis
from botocore.exceptions import ClientError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_aws_settings, get_ml_settings
from backend.core.exceptions import ModelNotLoadedError, NotFoundError, ValidationError
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData
from backend.schemas.strategy_schema import (
    CompetitorStrategyEntry,
    ExplanationFact,
    FeatureContributionResponse,
    LastIngestedSessionResponse,
    PitRecommendationExplanation,
    PitWindowResponse,
    StrategyOverviewResponse,
    StrategyPredictionHistoryEntry,
    StrategyPredictionHistoryResponse,
    UndercutThreatResponse,
)
from backend.services.cache_service import cacheable

# explainability/pit_predictor use the redundant "as X" alias, not a plain
# import — tests reach them via strategy_service.explainability/
# strategy_service.pit_predictor (the same reference this module's own code
# uses, so monkeypatching one affects the other), which mypy --strict's
# no_implicit_reexport check otherwise flags as an unexported cross-module
# attribute. tire_deg_model has no such external access pattern today.
from backend.services.ml import explainability as explainability
from backend.services.ml import pit_predictor as pit_predictor
from backend.services.ml import tire_deg_model
from backend.services.ml.race_simulator import LAP_TIME_NOISE_STD_SECONDS, PIT_STOP_SECONDS

logger = logging.getLogger(__name__)

# --- Model loading (see module docstring: duplicated from prediction_worker.py) ---

_MODEL_FILES = (
    "tire_deg_soft.pkl",
    "tire_deg_medium.pkl",
    "tire_deg_hard.pkl",
    "tire_deg_inter.pkl",
    "tire_deg_wet.pkl",
    "pit_predictor.pkl",
    "safety_car_model.pkl",
)
_COMPOUND_TO_MODEL_SUFFIX = {
    "SOFT": "soft",
    "MEDIUM": "medium",
    "HARD": "hard",
    "INTERMEDIATE": "inter",
    "WET": "wet",
}
_MODEL_VERSION_TAG = "production"
_COMPOUND_ENCODING = {"HARD": 0, "INTERMEDIATE": 1, "MEDIUM": 2, "SOFT": 3, "WET": 4}
# Same set as prediction_worker.py's identical constant — used only for the
# safety_car_model.probability_within wet_track argument, in
# get_pit_window_with_explanation's pit_predictor SHAP context.
_WET_COMPOUNDS = frozenset({"INTERMEDIATE", "WET"})

PIT_WINDOW_LOOKAHEAD_LAPS = 15
_STINT2_CANDIDATE_COMPOUNDS = ("SOFT", "MEDIUM", "HARD")
# Contiguous band, around the recommended pit_lap, of candidates whose
# projected_total_delta_seconds is within this many seconds of the optimal
# candidate's own delta — see build_pit_recommendation's window computation.
# Anchored to the same order of magnitude as tire_deg_model.
# DEGRADATION_THRESHOLD_SECONDS (1.5s) rather than picked arbitrarily: both
# describe "a lap-time difference small enough to not matter operationally."
PIT_WINDOW_TOLERANCE_SECONDS = 1.5
# Monte Carlo draws for build_pit_recommendation's confidence_score — pure
# numpy noise sampling + argmin, no per-sample model calls, so this can be
# large without a real runtime cost (unlike UNDERCUT_MONTE_CARLO_SIMS, which
# is deliberately smaller because its pre-vectorization history made 200 the
# most it was ever measured cheap at 3 predict() calls per invocation).
PIT_WINDOW_MONTE_CARLO_SIMS = 2000
# Noise-scale fallback for build_pit_recommendation's confidence Monte Carlo
# when a compound's tire_deg sidecar has no holdout_mae (a legacy sidecar
# predating train_models.py guaranteeing that key, or a compound with no
# model loaded at all) — a mid-range value against the real per-compound
# MAEs seen in this project's own training runs (documented in CLAUDE.md's
# Data Quality Notes: SOFT/MEDIUM/HARD in the 0.5-0.9s range pre-weather-
# revert; the stale 8-feature WET leftover at 5.79 is the outlier this
# default is deliberately NOT anchored to).
DEFAULT_HOLDOUT_MAE_SECONDS = 1.0
UNDERCUT_PROJECTION_LAPS = 5
UNDERCUT_MONTE_CARLO_SIMS = 200
COMPETITOR_STRATEGY_HORIZON_LAPS = 15
# Historical ingested data is immutable, so a long TTL is fine — the only
# thing that changes this result is a new ingest, which touches no Redis, so
# a newer race surfaces after this expires (or a manual cache_service delete).
LAST_INGESTED_SESSION_TTL_SECONDS = 86400

_model_cache: dict[str, Any] = {}
# Per tire_deg model filename, its own CategoricalEncodingMaps (or None if that
# model's sidecar is missing/legacy — predates the encoding-persistence fix).
# Populated as a side effect of _load_models(), same process lifetime as
# _model_cache — see _load_encoding_maps() and tire_deg_model.py's "Training-
# time categorical encoding" section.
_encoding_maps_cache: dict[str, Any] = {}
# Per tire_deg model filename, its own recovered holdout_mae (or None if that
# model's sidecar is missing/legacy) — populated alongside _encoding_maps_cache
# as a side effect of _load_models(), same reasoning. See _load_holdout_mae()
# and build_pit_recommendation's confidence computation.
_holdout_mae_cache: dict[str, float | None] = {}


def _local_model_path(filename: str) -> Path:
    model_dir = Path(get_ml_settings().model_cache_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir / filename


def _download_from_s3(filename: str) -> Path:
    """Download a model file from S3, unless already cached locally.

    Args:
        filename: Model file name, as listed in the ML Model Registry.
    Returns:
        Local filesystem path to the (now-)cached file.
    """
    path = _local_model_path(filename)
    if path.exists():
        return path

    settings = get_aws_settings()
    client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    client.download_file(settings.aws_bucket_name, f"{_MODEL_VERSION_TAG}/{filename}", str(path))
    return path


def _local_metrics_path(filename: str) -> Path:
    model_dir = Path(get_ml_settings().model_cache_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir / f"{filename}.metrics.json"


def _download_metrics_from_s3(filename: str) -> dict[str, Any] | None:
    """Download a tire_deg model's own sidecar metrics.json from S3, unless cached locally.

    Same local-disk-cache-then-fetch lifecycle as _download_from_s3 (never re-fetches
    once cached, so — like every other model in this process — a worker restart is
    what picks up a newly-promoted model's fresh sidecar, not a background refresh).

    Args:
        filename: Model file name, e.g. "tire_deg_medium.pkl" — fetches its
            {_MODEL_VERSION_TAG}/{filename}.metrics.json sidecar, not the model itself.
    Returns:
        The sidecar's parsed JSON contents, or None if no sidecar exists for this
        filename yet (a production model that predates train_models.py writing one at
        all, or the item-9 schema-check fix specifically) — callers must treat None as
        "no recoverable encoding map," not as an error.
    """
    path = _local_metrics_path(filename)
    if path.exists():
        return dict(json.loads(path.read_text()))

    settings = get_aws_settings()
    client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    try:
        obj = client.get_object(
            Bucket=settings.aws_bucket_name, Key=f"{_MODEL_VERSION_TAG}/{filename}.metrics.json"
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise
    body = obj["Body"].read()
    path.write_bytes(body)
    return dict(json.loads(body))


def _load_models() -> dict[str, Any]:
    """Load all registry models into an in-process cache, downloading from S3 on first use.

    Also populates _encoding_maps_cache with each tire_deg model's own recovered
    training-time driver/circuit code map (see tire_deg_model.py's "Training-time
    categorical encoding" section) and _holdout_mae_cache with each tire_deg
    model's own recovered holdout_mae (see build_pit_recommendation's confidence
    computation) — same once-per-process lifecycle as the models themselves, so
    this function is the single place all three caches get populated together,
    which is what lets apply_incompatible_model_fallbacks alias all three in
    lockstep below.

    Args:
        None.
    Returns:
        Mapping of model filename to the deserialised model object.
    """
    if _model_cache:
        return _model_cache
    for filename in _MODEL_FILES:
        _model_cache[filename] = joblib.load(_download_from_s3(filename))
    for filename in _MODEL_FILES:
        if filename.startswith("tire_deg_"):
            metrics = _download_metrics_from_s3(filename)
            _encoding_maps_cache[filename] = tire_deg_model.encoding_maps_from_metrics(metrics)
            _holdout_mae_cache[filename] = tire_deg_model.holdout_mae_from_metrics(metrics)
    # Guards against a stale/schema-incompatible production model (e.g. the
    # 8-feature tire_deg_wet.pkl leftover from the reverted weather
    # experiment — see docs/simulator-issues-wet-model-and-position-
    # context.md) by aliasing it (and its encoding maps/holdout_mae) to a
    # compatible fallback for this process.
    tire_deg_model.apply_incompatible_model_fallbacks(
        _model_cache, _encoding_maps_cache, _holdout_mae_cache
    )
    return _model_cache


def _load_encoding_maps() -> dict[str, tire_deg_model.CategoricalEncodingMaps | None]:
    """This process's tire_deg encoding-maps cache, populated as a side effect of _load_models().

    Args:
        None.
    Returns:
        Mapping of tire_deg model filename to its CategoricalEncodingMaps, or None for a
        filename whose sidecar is missing/legacy — see resolve_driver_code/
        resolve_circuit_code (tire_deg_model.py) for how a None entry is handled.
    """
    _load_models()
    return _encoding_maps_cache


def _load_holdout_mae() -> dict[str, float | None]:
    """This process's tire_deg holdout-MAE cache, populated as a side effect of _load_models().

    Args:
        None.
    Returns:
        Mapping of tire_deg model filename to its holdout_mae, or None for a
        filename whose sidecar is missing/legacy — see _holdout_mae_for_compound
        for how a None entry is handled.
    """
    _load_models()
    return _holdout_mae_cache


def _pipeline_for_compound(models: dict[str, Any], compound: str) -> Any | None:
    """Look up the tire_deg pipeline for a compound, defaulting to MEDIUM's suffix."""
    suffix = _COMPOUND_TO_MODEL_SUFFIX.get(compound, "medium")
    return models.get(f"tire_deg_{suffix}.pkl")


def _encoding_maps_for_compound(
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None], compound: str
) -> tire_deg_model.CategoricalEncodingMaps | None:
    """Look up the tire_deg encoding maps for a compound, defaulting to MEDIUM's suffix.

    Mirrors _pipeline_for_compound's exact suffix lookup/default — a driver/circuit
    code must always be resolved against the SAME model's own map as the pipeline
    it's about to be fed into (see tire_deg_model.py's "Training-time categorical
    encoding" section for why one shared map can't be used across compounds).

    Args:
        maps_cache: Output of _load_encoding_maps().
        compound: Tyre compound name.
    Returns:
        That compound's CategoricalEncodingMaps, or None if unavailable — callers pass
        this straight to resolve_driver_code/resolve_circuit_code, which already treat
        None as "use the crc32 fallback."
    """
    suffix = _COMPOUND_TO_MODEL_SUFFIX.get(compound, "medium")
    return maps_cache.get(f"tire_deg_{suffix}.pkl")


def _holdout_mae_for_compound(mae_cache: dict[str, float | None], compound: str) -> float:
    """This compound's tire_deg holdout_mae, defaulting to MEDIUM's suffix.

    Mirrors _encoding_maps_for_compound's exact suffix lookup/default, except the
    final fallback is a concrete float (DEFAULT_HOLDOUT_MAE_SECONDS) rather than
    None — every caller of this needs a real noise-scale number to sample from,
    unlike resolve_driver_code/resolve_circuit_code's crc32 fallback which needs
    no caller-side default handling.

    Args:
        mae_cache: Output of _load_holdout_mae().
        compound: Tyre compound name.
    Returns:
        That compound's holdout_mae, or DEFAULT_HOLDOUT_MAE_SECONDS if
        unavailable (legacy sidecar, or no model loaded for this compound).
    """
    suffix = _COMPOUND_TO_MODEL_SUFFIX.get(compound, "medium")
    mae = mae_cache.get(f"tire_deg_{suffix}.pkl")
    return mae if mae is not None else DEFAULT_HOLDOUT_MAE_SECONDS


# --- Shared DB helpers ---


async def resolve_season_round(db: AsyncSession, session_id: uuid.UUID) -> tuple[int, int]:
    """Resolve a session's (season, round_number) via its parent race.

    Public (no leading underscore) since the API route needs it to bridge a
    session_id-only path into the (season, round_number)-keyed cache functions
    below — same duplicated-pattern rationale as telemetry_service.py's
    identical helper (both resolve the same thing, from different services,
    since services must not import each other).

    Args:
        db: Async DB session.
        session_id: Session to resolve.
    Returns:
        (season, round_number).
    Raises:
        NotFoundError: No session with this ID exists.
    """
    query = (
        select(Race.season, Race.round_number)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .where(SessionModel.id == session_id)
    )
    row = (await db.execute(query)).one_or_none()
    if row is None:
        raise NotFoundError(f"Session {session_id} not found")
    return int(row[0]), int(row[1])


async def validate_current_lap(db: AsyncSession, session_id: uuid.UUID, current_lap: int) -> None:
    """Reject a Strategy Simulator request whose current_lap is impossible for this session.

    Two checks, in order:
    1. session_id must resolve to a real session — otherwise the request's
       current_lap is meaningless, and the failure should be a clear 404
       raised here, not a raw NoResultFound surfacing later out of
       prediction_worker._build_race_state's own unrelated context query.
    2. current_lap must be at most one lap past this session's real
       progress: MAX(LapData.lap_number) across the whole session, or 0 if
       no lap_data exists yet for it at all (a genuine pre-race what-if —
       see tests/integration/test_strategy_endpoint.py's
       test_simulate_returns_task_id, which seeds zero LapData rows and
       expects current_lap=1 to succeed). "One past" allows claiming to be
       currently completing the very next lap after the last one anyone in
       the field has finished; current_lap any further ahead is either
       stale client state or a fabricated race length the session never
       had — see docs/simulator-issues-wet-model-and-position-context.md's
       Checkpoint-6 follow-up finding (a current_lap=68 what-if was
       silently accepted for a session whose real race was 44 laps).

    Public (no leading underscore): called from both
    apis/v1/strategy.py's simulate_strategy (before enqueueing the Celery
    task) and prediction_worker._run_simulation (defense in depth — a
    caller that enqueues run_race_simulation directly, bypassing the route
    entirely, e.g. a future replay/backfill script, must not be able to
    skip this check just by not going through the API).

    Args:
        db: Async DB session.
        session_id: Session the what-if is for.
        current_lap: The request's claimed current lap.
    Returns:
        None.
    Raises:
        NotFoundError: No session with this ID exists.
        ValidationError: current_lap exceeds this session's real progress + 1.
    """
    session_exists = (
        await db.execute(select(SessionModel.id).where(SessionModel.id == session_id))
    ).scalar_one_or_none()
    if session_exists is None:
        raise NotFoundError(f"Session {session_id} not found")

    max_ingested_lap = (
        await db.execute(
            select(func.max(LapData.lap_number)).where(LapData.session_id == session_id)
        )
    ).scalar_one_or_none()
    ceiling = (max_ingested_lap or 0) + 1
    if current_lap > ceiling:
        latest = max_ingested_lap if max_ingested_lap is not None else "none"
        raise ValidationError(
            f"current_lap ({current_lap}) exceeds session {session_id}'s real progress "
            f"(latest ingested lap: {latest}, max valid current_lap: {ceiling})"
        )


async def _current_state(
    db: AsyncSession, session_id: uuid.UUID, driver_id: uuid.UUID
) -> dict[str, Any]:
    """Latest lap + circuit + estimated total-laps context for one driver in a session.

    Args:
        db: Async DB session.
        session_id: Session to read.
        driver_id: Driver to read.
    Returns:
        Dict with lap_number, compound, tyre_age_laps, position, total_laps, circuit_id,
        circuit_name (the latter needed to resolve this driver/circuit's real
        training-time tire_deg encoding — see tire_deg_model.resolve_driver_code/
        resolve_circuit_code).
    Raises:
        NotFoundError: No lap_data row exists yet for this driver/session.
    """
    lap_query = (
        select(LapData)
        .where(LapData.session_id == session_id, LapData.driver_id == driver_id)
        .order_by(LapData.lap_number.desc())
        .limit(1)
    )
    lap = (await db.execute(lap_query)).scalar_one_or_none()
    if lap is None:
        raise NotFoundError(f"No lap data for driver {driver_id} in session {session_id}")

    total_laps_query = select(func.max(LapData.lap_number)).where(LapData.session_id == session_id)
    total_laps = (await db.execute(total_laps_query)).scalar_one() or lap.lap_number

    circuit_query = (
        select(Race.circuit_id, Circuit.name)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .join(Circuit, Race.circuit_id == Circuit.id)
        .where(SessionModel.id == session_id)
    )
    circuit_id, circuit_name = (await db.execute(circuit_query)).one()

    return {
        "lap_number": lap.lap_number,
        "compound": lap.compound,
        "tyre_age_laps": lap.tyre_age_laps,
        "position": lap.position,
        "total_laps": int(total_laps),
        "circuit_id": circuit_id,
        "circuit_name": circuit_name,
    }


async def _cumulative_race_time(
    db: AsyncSession, session_id: uuid.UUID, driver_id: uuid.UUID, up_to_lap: int
) -> float:
    """Elapsed race time for one driver through up_to_lap.

    Prefers LapData.session_elapsed_seconds (a real absolute elapsed time
    from the driver's latest ingested lap at or before up_to_lap — populated
    for a backfilled historical session, comparable across drivers
    regardless of differing NULL-lap-time counts; see CLAUDE.md Deferred
    Wiring item A and backfill_lap_session_time.py). Falls back to the
    original SUM(lap_time_seconds) reconstruction when no such row exists —
    a live-ingested session (never backfilled) or a driver with no laps yet
    through up_to_lap; either case collapses to the same `elapsed is None`
    check, and the SUM fallback already returns 0.0 in the latter case, so
    no separate branch is needed to tell them apart.

    Args:
        db: Async DB session.
        session_id: Session to query.
        driver_id: Driver to query.
        up_to_lap: Last lap number (inclusive) to sum.
    Returns:
        Cumulative elapsed race time in seconds; 0.0 if no laps recorded yet.
    """
    latest_row_query = (
        select(LapData.session_elapsed_seconds)
        .where(
            LapData.session_id == session_id,
            LapData.driver_id == driver_id,
            LapData.lap_number <= up_to_lap,
        )
        .order_by(LapData.lap_number.desc())
        .limit(1)
    )
    elapsed = (await db.execute(latest_row_query)).scalar_one_or_none()
    if elapsed is not None:
        return float(elapsed)

    sum_query = select(func.sum(LapData.lap_time_seconds)).where(
        LapData.session_id == session_id,
        LapData.driver_id == driver_id,
        LapData.lap_number <= up_to_lap,
        LapData.lap_time_seconds.is_not(None),
    )
    return float((await db.execute(sum_query)).scalar_one() or 0.0)


def _weather_key(season: int, round_number: int) -> str:
    return f"f1:{season}:{round_number}:weather:latest"


async def _resolve_weather(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    circuit_id: uuid.UUID,
    compound: str,
) -> tuple[float, float]:
    """Current track_temp/air_temp for a tire_deg inference feature vector.

    Prefers the live f1:{season}:{round}:weather:latest key (written by
    ingest_live_session.py's WeatherData handler). Falls back to a DB average
    over the same circuit+compound when that key is absent (pre-race, or a
    historical session with no live ingestor run) — see module docstring.

    Args:
        client: Redis client.
        db: Async DB session.
        season, round_number: Race weekend identifiers.
        circuit_id: Circuit to average over on fallback.
        compound: Compound to average over on fallback.
    Returns:
        (track_temp, air_temp) in Celsius.
    """
    raw = await client.get(_weather_key(season, round_number))
    if raw is not None:
        parsed = json.loads(raw)
        return float(parsed["track_temp"]), float(parsed["air_temp"])

    query = (
        select(func.avg(LapData.track_temp), func.avg(LapData.air_temp))
        .join(SessionModel, LapData.session_id == SessionModel.id)
        .join(Race, SessionModel.race_id == Race.id)
        .where(
            Race.circuit_id == circuit_id,
            LapData.compound == compound,
            LapData.track_temp.is_not(None),
        )
    )
    avg_track_temp, avg_air_temp = (await db.execute(query)).one()
    return (
        float(avg_track_temp)
        if avg_track_temp is not None
        else tire_deg_model.DEFAULT_TRACK_TEMP_C,
        float(avg_air_temp) if avg_air_temp is not None else tire_deg_model.DEFAULT_AIR_TEMP_C,
    )


# --- tire_deg projection ---


def _project_stint_delta(
    pipeline: Any,
    compound_encoded: int,
    driver_code: int,
    circuit_code: int,
    start_lap: int,
    n_laps: int,
    start_tyre_age: int,
    total_laps: int,
) -> float:
    """Sum of tire_deg-predicted lap_time_delta over n_laps starting at start_lap.

    Args:
        pipeline: Fitted tire_deg_model pipeline for the relevant compound.
        compound_encoded, driver_code, circuit_code: Encoded categorical features
            (see module docstring for the encoding caveat).
        start_lap: First lap number of this stint segment.
        n_laps: Number of laps to project.
        start_tyre_age: Tyre age at start_lap.
        total_laps: Estimated race distance, for the fuel_adjusted_time feature.
    Returns:
        Sum of predicted per-lap deltas in seconds; 0.0 if n_laps <= 0.
    """
    if n_laps <= 0:
        return 0.0
    laps = np.arange(start_lap, start_lap + n_laps, dtype=np.float64)
    tyre_age = start_tyre_age + np.arange(n_laps, dtype=np.float64)
    fuel_at_lap = tire_deg_model.ASSUMED_START_FUEL_KG * (1 - laps / max(total_laps, 1))
    fuel_adjusted_time = -tire_deg_model.FUEL_TIME_PENALTY_PER_KG * (
        tire_deg_model.ASSUMED_START_FUEL_KG - fuel_at_lap
    )
    features = np.column_stack(
        [
            laps,
            np.full(n_laps, float(compound_encoded)),
            tyre_age,
            fuel_adjusted_time,
            np.full(n_laps, float(circuit_code)),
            np.full(n_laps, float(driver_code)),
        ]
    )
    result: float = float(pipeline.predict(features).sum())
    return result


def _sampled_noise(rng: np.random.Generator, n_laps: int, n_samples: int) -> np.ndarray:
    """n_samples independent Monte Carlo noise draws for one stint segment.

    Variance scales with n_laps (the sum of n_laps iid per-lap noise terms),
    reusing race_simulator.LAP_TIME_NOISE_STD_SECONDS for consistency with the
    Day 8 simulator's noise assumption — same distribution _sampled_stint_delta
    used to draw one value at a time.

    Args:
        rng: Shared numpy Generator.
        n_laps: Stint length in laps.
        n_samples: Number of Monte Carlo draws (UNDERCUT_MONTE_CARLO_SIMS).
    Returns:
        Array of n_samples noise values; all zero if n_laps <= 0 (matches the
        old per-draw helper's "empty stint, no noise" behavior).
    """
    if n_laps <= 0:
        return np.zeros(n_samples)
    return rng.normal(0.0, LAP_TIME_NOISE_STD_SECONDS * math.sqrt(n_laps), size=n_samples)


# --- build_pit_recommendation ---


def _key_pit_window(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
) -> str:
    return f"f1:{season}:{round_number}:strategy:{driver_id}:pit_window"


def _stint2_batch_deltas(
    pipeline: Any,
    compound_encoded: int,
    driver_code: int,
    circuit_code: int,
    pit_laps: np.ndarray,
    laps_remaining: np.ndarray,
    total_laps: int,
) -> np.ndarray:
    """One predict() call for a compound's stint-2 delta, across EVERY pit_lap
    candidate at once — the batching this function exists for.

    Unlike stint 1 (whose tyre-age trajectory at a given absolute lap number
    is identical for every pit_lap candidate — it's the same ongoing stint
    regardless of when it eventually ends), stint 2's fresh tyre resets at a
    DIFFERENT lap for every candidate, so the same absolute lap number needs
    a different tyre_age depending on which pit_lap it's being evaluated for.
    That rules out a single 1D arange + cumsum (stint 1's approach, still used
    in build_pit_recommendation directly). Instead this builds a padded 2D
    grid — rows are pit_lap candidates, columns are the lap offset within that
    candidate's own stint 2 — exactly the same "2D grid, one batched predict(),
    reshape, mask, reduce" pattern tire_deg_model.predict_life_remaining_batch
    already uses for its own per-row lookahead sweep, just batching across
    candidates here instead of across drivers.

    Args:
        pipeline: Fitted tire_deg_model pipeline for this candidate compound.
        compound_encoded, driver_code, circuit_code: Encoded categorical
            features (see module docstring for the encoding caveat) — all
            constant across every candidate (same driver/circuit, only the
            stint-2 compound choice varies across the 3 calls this makes).
        pit_laps: 1D array of candidate pit lap numbers.
        laps_remaining: 1D array, same length as pit_laps — total_laps - pit_lap
            for each candidate (stint 2's length on this compound).
        total_laps: Estimated race distance, for the fuel_adjusted_time feature.
    Returns:
        1D array, same length as pit_laps: this compound's projected stint-2
        delta for each candidate pit_lap. All zero if pit_laps is empty or
        every candidate's laps_remaining is <= 0 (pitting on/after the last lap).
    """
    n = len(pit_laps)
    max_remaining = int(laps_remaining.max()) if n else 0
    if max_remaining <= 0:
        return np.zeros(n)

    offsets = np.arange(max_remaining, dtype=np.float64)
    lap_grid = pit_laps[:, None] + 1 + offsets[None, :]
    tyre_age_grid = np.broadcast_to(offsets[None, :], (n, max_remaining))
    valid = offsets[None, :] < laps_remaining[:, None]

    fuel_at_lap = tire_deg_model.ASSUMED_START_FUEL_KG * (1 - lap_grid / max(total_laps, 1))
    fuel_adjusted_time = -tire_deg_model.FUEL_TIME_PENALTY_PER_KG * (
        tire_deg_model.ASSUMED_START_FUEL_KG - fuel_at_lap
    )
    flat_features = np.column_stack(
        [
            lap_grid.ravel(),
            np.full(n * max_remaining, float(compound_encoded)),
            tyre_age_grid.ravel(),
            fuel_adjusted_time.ravel(),
            np.full(n * max_remaining, float(circuit_code)),
            np.full(n * max_remaining, float(driver_code)),
        ]
    )
    preds = pipeline.predict(flat_features).reshape(n, max_remaining)
    result: np.ndarray = np.where(valid, preds, 0.0).sum(axis=1)
    return result


@cacheable(ttl=30, key_fn=_key_pit_window)
async def build_pit_recommendation(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Rank candidate pit laps by projected total race time, return the top 3.

    For each candidate pit lap in [current_lap+1, current_lap+PIT_WINDOW_LOOKAHEAD_LAPS]
    (capped at the estimated race end), projects the stint-to-pit-lap delta on the
    current compound plus PIT_STOP_SECONDS plus the best of _STINT2_CANDIDATE_COMPOUNDS'
    stint-from-pit-lap-to-race-end delta — same model as before this function's
    rewrite, just computed differently:

    - **Batched, not looped.** The original implementation called
      pipeline.predict() once per (pit_lap, segment) combination — up to
      1 + 3 = 4 calls per pit_lap candidate, 15 candidates, ~60 calls per
      invocation. Stint 1's tyre-age trajectory at a given absolute lap
      number is IDENTICAL for every pit_lap candidate (it's the same ongoing
      stint regardless of when it ends), so it collapses to one predict()
      call over the full candidate range plus a cumulative sum. Stint 2's
      fresh-tyre age resets differently per candidate, so it can't cumsum the
      same way — but batches into one predict() call PER CANDIDATE COMPOUND
      (3 total) via _stint2_batch_deltas' padded 2D grid, covering all 15
      pit_lap candidates in that single call. Total: 4 predict() calls
      regardless of PIT_WINDOW_LOOKAHEAD_LAPS, down from ~60 — same class of
      win as the undercut/overcut vectorization (CLAUDE.md's Deferred Wiring,
      42x on that endpoint), same reasoning: the deterministic tire_deg
      projection doesn't vary per unit of the thing being looped over any
      more than it strictly has to.
    - **The winning stint-2 compound is kept, not discarded.** The original
      loop tracked only best_stint2_delta (a float), never which compound
      produced it — recommended_compound now survives per candidate.
    - **window_start/window_end is a real narrow band**, not the fixed
      PIT_WINDOW_LOOKAHEAD_LAPS search horizon rendered as if it were a
      recommendation. See the window computation below.
    - **confidence_score** is now populated (previously didn't exist in any
      form). See the Monte Carlo block below.

    Args:
        client: Redis client (cache-aside — first positional arg per cacheable's contract).
        db: Async DB session.
        season, round_number: Race weekend identifiers, for the cache key.
        session_id: Session to evaluate.
        driver_id: Driver to plan a pit window for.
    Returns:
        Up to 3 dicts (pit_lap, window_start, window_end,
        projected_total_delta_seconds, recommended_compound, confidence_score),
        ascending by projected_total_delta_seconds (lower = better).
        window_start/window_end are identical across all returned candidates
        (the tolerance band around the #1 candidate — see below); confidence_score
        is populated only for the #1 (rank 0) candidate, None for the rest — it
        answers "how sure is the model that THIS recommendation is optimal,"
        which isn't a meaningful question to ask of a candidate that isn't
        being recommended.
    Raises:
        NotFoundError: No lap_data exists yet for this driver/session (via
            _current_state) — the API layer surfaces this as an HTTP 404,
            which is correct semantics here, not a gap to paper over with a
            null-fields response.
        ModelNotLoadedError: No tire degradation model loaded for the
            driver's current compound, or for every _STINT2_CANDIDATE_COMPOUNDS
            entry (nothing to recommend pitting onto).
    """
    models = _load_models()
    maps_cache = _load_encoding_maps()
    mae_cache = _load_holdout_mae()
    state = await _current_state(db, session_id, driver_id)
    return compute_pit_recommendation(models, maps_cache, mae_cache, driver_id, state)


def compute_pit_recommendation(
    models: dict[str, Any],
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None],
    mae_cache: dict[str, float | None],
    driver_id: uuid.UUID,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Pure computation core of build_pit_recommendation — no DB/cache/model-
    loading side effects.

    Split out (Checkpoint 4) so a caller that already has its own resolved
    `state` can invoke the SAME batched counterfactual search directly,
    bypassing both the @cacheable layer above (irrelevant to a one-shot
    caller, not a repeated-request pattern) and _current_state's own DB
    round trip. The one real caller today is prediction_worker.
    _compute_recommendation_fields: it MUST NOT call _current_state (or
    build_pit_recommendation itself) for this same lap, since process_lap
    (a separate, independently-ordered Celery task) may not have committed
    this lap's own LapData row yet — see that function's own docstring.
    build_pit_recommendation above still resolves state itself, for the
    REST-endpoint caller that has no such context already in hand.

    Args:
        models: Loaded model registry, keyed by filename (this process's own
            cache — strategy_service's or a duplicated caller's, e.g.
            prediction_worker's; this function doesn't care which).
        maps_cache: Output of _load_encoding_maps() (or an equivalent cache).
        mae_cache: Output of _load_holdout_mae() (or an equivalent cache).
        driver_id: Driver to plan a pit window for.
        state: Dict with compound, tyre_age_laps, lap_number, total_laps,
            circuit_name — the same shape _current_state returns (only
            these 5 keys are read here).
    Returns:
        Same as build_pit_recommendation.
    Raises:
        ModelNotLoadedError: No tire degradation model loaded for the
            current compound, or for every _STINT2_CANDIDATE_COMPOUNDS entry.
    """
    current_maps = _encoding_maps_for_compound(maps_cache, state["compound"])
    driver_code = tire_deg_model.resolve_driver_code(current_maps, str(driver_id))
    circuit_code = tire_deg_model.resolve_circuit_code(current_maps, state["circuit_name"])
    current_compound_encoded = _COMPOUND_ENCODING.get(
        state["compound"], _COMPOUND_ENCODING["MEDIUM"]
    )
    current_pipeline = _pipeline_for_compound(models, state["compound"])
    if current_pipeline is None:
        raise ModelNotLoadedError(
            f"No tire degradation model loaded for compound {state['compound']}"
        )
    current_mae = _holdout_mae_for_compound(mae_cache, state["compound"])

    max_pit_lap = min(state["lap_number"] + PIT_WINDOW_LOOKAHEAD_LAPS, state["total_laps"])
    pit_laps = np.arange(state["lap_number"] + 1, max_pit_lap + 1)
    n_candidates = len(pit_laps)
    if n_candidates == 0:
        return []

    # --- Stint 1: one predict() call, cumulative sum. ---
    # laps_stint1[i] == pit_laps[i] by construction (both range over
    # [current_lap+1, max_pit_lap]) — pit_laps[i] IS the last lap of stint 1
    # for candidate i (the driver still completes that lap before pitting),
    # so np.cumsum(per-lap deltas)[i] is exactly stint 1's total delta for
    # pit_laps[i], with no separate indexing needed to line the two up.
    laps_stint1 = pit_laps.astype(np.float64)
    tyre_age_stint1 = state["tyre_age_laps"] + np.arange(1, n_candidates + 1, dtype=np.float64)
    fuel_at_lap1 = tire_deg_model.ASSUMED_START_FUEL_KG * (
        1 - laps_stint1 / max(state["total_laps"], 1)
    )
    fuel_adjusted_time1 = -tire_deg_model.FUEL_TIME_PENALTY_PER_KG * (
        tire_deg_model.ASSUMED_START_FUEL_KG - fuel_at_lap1
    )
    features1 = np.column_stack(
        [
            laps_stint1,
            np.full(n_candidates, float(current_compound_encoded)),
            tyre_age_stint1,
            fuel_adjusted_time1,
            np.full(n_candidates, float(circuit_code)),
            np.full(n_candidates, float(driver_code)),
        ]
    )
    stint1_delta = np.cumsum(current_pipeline.predict(features1))

    # --- Stint 2: one predict() call PER CANDIDATE COMPOUND (see
    # _stint2_batch_deltas), each covering all n_candidates pit laps at once. ---
    laps_remaining = state["total_laps"] - pit_laps
    stint2_delta_by_compound: dict[str, np.ndarray] = {}
    mae_by_compound: dict[str, float] = {}
    for candidate_compound in _STINT2_CANDIDATE_COMPOUNDS:
        pipeline = _pipeline_for_compound(models, candidate_compound)
        if pipeline is None:
            continue
        candidate_maps = _encoding_maps_for_compound(maps_cache, candidate_compound)
        candidate_driver_code = tire_deg_model.resolve_driver_code(candidate_maps, str(driver_id))
        candidate_circuit_code = tire_deg_model.resolve_circuit_code(
            candidate_maps, state["circuit_name"]
        )
        stint2_delta_by_compound[candidate_compound] = _stint2_batch_deltas(
            pipeline,
            _COMPOUND_ENCODING[candidate_compound],
            candidate_driver_code,
            candidate_circuit_code,
            pit_laps,
            laps_remaining,
            state["total_laps"],
        )
        mae_by_compound[candidate_compound] = _holdout_mae_for_compound(
            mae_cache, candidate_compound
        )

    if not stint2_delta_by_compound:
        raise ModelNotLoadedError(
            "No tire degradation model loaded for any stint-2 candidate compound"
        )

    compound_names = list(stint2_delta_by_compound.keys())
    stint2_matrix = np.column_stack([stint2_delta_by_compound[c] for c in compound_names])
    best_stint2_idx = np.argmin(stint2_matrix, axis=1)
    best_stint2_delta = stint2_matrix[np.arange(n_candidates), best_stint2_idx]
    winning_compound = [compound_names[i] for i in best_stint2_idx]

    total_delta = stint1_delta + PIT_STOP_SECONDS + best_stint2_delta

    order = np.argsort(total_delta)
    best_idx = int(order[0])

    # --- Narrow window: the contiguous band of candidates around the
    # recommendation whose own total_delta is within PIT_WINDOW_TOLERANCE_
    # SECONDS of the optimum — expanded outward from best_idx rather than
    # taking every candidate under the threshold globally, since the delta
    # curve isn't guaranteed perfectly unimodal (model noise) and a
    # non-contiguous "window" would be a meaningless range to display. ---
    threshold = total_delta[best_idx] + PIT_WINDOW_TOLERANCE_SECONDS
    window_start_idx = best_idx
    while window_start_idx > 0 and total_delta[window_start_idx - 1] <= threshold:
        window_start_idx -= 1
    window_end_idx = best_idx
    while window_end_idx < n_candidates - 1 and total_delta[window_end_idx + 1] <= threshold:
        window_end_idx += 1
    window_start = int(pit_laps[window_start_idx])
    window_end = int(pit_laps[window_end_idx])

    # --- Confidence: vectorized Monte Carlo — P(the recommended candidate is
    # STILL the argmin once every candidate's total_delta is perturbed by
    # noise scaled to ITS OWN model uncertainty). Each candidate's noise std
    # is derived from the holdout_mae of the actual model(s) that produced
    # its total_delta (current compound for stint 1, that candidate's own
    # winning compound for stint 2), summed by segment length under an iid-
    # per-lap-error assumption (std scales with sqrt(n_laps) — same
    # noise-aggregation principle as _sampled_noise above, just per-compound
    # MAE instead of one shared LAP_TIME_NOISE_STD_SECONDS constant) and
    # combined in quadrature (independent noise sources). No per-sample model
    # calls — pure numpy sampling + argmin, same vectorization style as
    # _undercut_overcut_probability's own Monte Carlo. ---
    stint1_laps_by_candidate = np.arange(1, n_candidates + 1, dtype=np.float64)
    winning_mae = np.array(
        [mae_by_compound.get(c, DEFAULT_HOLDOUT_MAE_SECONDS) for c in winning_compound]
    )
    stint1_std = current_mae * np.sqrt(stint1_laps_by_candidate)
    stint2_std = winning_mae * np.sqrt(np.maximum(laps_remaining, 0).astype(np.float64))
    combined_std = np.sqrt(stint1_std**2 + stint2_std**2)

    rng = np.random.default_rng()
    noise = rng.standard_normal((n_candidates, PIT_WINDOW_MONTE_CARLO_SIMS)) * combined_std[:, None]
    noisy_delta = total_delta[:, None] + noise
    winners = np.argmin(noisy_delta, axis=0)
    confidence = float(np.mean(winners == best_idx))

    candidates: list[dict[str, Any]] = []
    for rank, idx in enumerate(order[:3]):
        idx_int = int(idx)
        candidates.append(
            {
                "pit_lap": int(pit_laps[idx_int]),
                "window_start": window_start,
                "window_end": window_end,
                "projected_total_delta_seconds": float(total_delta[idx_int]),
                "recommended_compound": winning_compound[idx_int],
                "confidence_score": confidence if rank == 0 else None,
            }
        )
    return candidates


# --- get_pit_window_with_explanation: combined tire_deg + pit_predictor explanation ---


async def _resolve_field_neighbors_from_redis(
    client: aioredis.Redis,  # type: ignore[type-arg]
    season: int,
    round_number: int,
    driver_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Field position/gaps from the live-authoritative f1:{season}:{round}:gaps key.

    Duplicated from prediction_worker._resolve_position_context_from_redis
    (same no-cross-service-import convention as this module's other
    duplicated helpers — see module docstring) — identical contract, reused
    here so get_pit_window_with_explanation's pit_predictor gap features can
    fall back to the same live-authoritative source the per-lap prediction
    pipeline already does (CLAUDE.md's core-feature-rebuild Checkpoint 1),
    rather than silently defaulting to "no neighbours" whenever
    _resolve_field_neighbors' own bounded lap_data query can't resolve a
    position.

    Args:
        client: Redis client.
        season, round_number: Race weekend identifiers, for the key.
        driver_id: Driver to locate within the field.
    Returns:
        Same shape as _resolve_field_neighbors' return value, or None if the
        key is missing/unparsable/this driver isn't in it — callers must
        treat None as "no live-gaps fallback available," not an error.
    """
    raw = await client.get(f"f1:{season}:{round_number}:gaps")
    if raw is None:
        return None
    try:
        entries = json.loads(raw)["gaps"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None

    index = next(
        (i for i, entry in enumerate(entries) if entry.get("driver_id") == str(driver_id)), None
    )
    if index is None or not isinstance(entries[index].get("position"), int):
        return None

    def _capped_gap(value: Any) -> float:
        if value is None:
            return pit_predictor.MAX_GAP_SECONDS
        return min(max(float(value), 0.0), pit_predictor.MAX_GAP_SECONDS)

    driver_entry = entries[index]
    target_ahead_driver_id = None
    gap_to_car_ahead = pit_predictor.MAX_GAP_SECONDS
    if index > 0:
        gap_to_car_ahead = _capped_gap(driver_entry.get("gap_to_ahead_seconds"))
        target_ahead_driver_id = uuid.UUID(entries[index - 1]["driver_id"])

    target_behind_driver_id = None
    gap_to_car_behind = pit_predictor.MAX_GAP_SECONDS
    if index + 1 < len(entries):
        gap_to_car_behind = _capped_gap(driver_entry.get("gap_to_behind_seconds"))
        target_behind_driver_id = uuid.UUID(entries[index + 1]["driver_id"])

    return {
        "position": int(driver_entry["position"]),
        "gap_to_car_ahead": gap_to_car_ahead,
        "gap_to_car_behind": gap_to_car_behind,
        "target_ahead_driver_id": target_ahead_driver_id,
        "target_behind_driver_id": target_behind_driver_id,
    }


async def _resolve_field_neighbors(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    current_lap: int,
    season: int,
    round_number: int,
) -> dict[str, Any]:
    """Current field position and immediate track-position neighbors for one driver.

    Duplicated from prediction_worker._resolve_position_context (same
    no-cross-service-import convention as this module's other duplicated
    helpers) — identical contract: bounded to lap_number <= current_lap,
    since an unbounded "latest lap per driver" query would read every OTHER
    driver's FINAL race position for a fully-ingested/replayed session,
    regardless of the requesting driver's own current lap (the exact bug
    CLAUDE.md's Deferred Wiring entry documented and Checkpoint 1 of this
    rebuild fixed on prediction_worker's own call site — this duplicates
    that same fix onto the second call site that needed it, rather than
    silently reintroducing it here). Falls back to the live-authoritative
    Redis gaps key (_resolve_field_neighbors_from_redis) when the bounded
    query can't resolve driver_id's own position.

    Args:
        client: Redis client, for the live-gaps fallback.
        db: Async DB session.
        session_id: Session to read.
        driver_id: Driver to locate within the field.
        current_lap: Only consider lap_data rows at or before this lap.
        season, round_number: Race weekend identifiers, for the Redis
            fallback's key.
    Returns:
        Dict with position, gap_to_car_ahead, gap_to_car_behind,
        target_ahead_driver_id, target_behind_driver_id. The two target ids
        are None for the leader/last car, and all fields fall back to
        MAX_GAP_SECONDS/no-target/back-of-field when driver_id has no
        resolvable position at all.
    """
    subq = (
        select(LapData.driver_id, func.max(LapData.lap_number).label("max_lap"))
        .where(LapData.session_id == session_id, LapData.lap_number <= current_lap)
        .group_by(LapData.driver_id)
        .subquery()
    )
    join_condition = (LapData.driver_id == subq.c.driver_id) & (
        LapData.lap_number == subq.c.max_lap
    )
    query = (
        select(LapData)
        .join(subq, join_condition)
        .where(LapData.session_id == session_id, LapData.position.is_not(None))
        .order_by(LapData.position)
    )
    field = list((await db.execute(query)).scalars().all())
    index = next((i for i, lap in enumerate(field) if lap.driver_id == driver_id), None)

    if index is None:
        redis_result = await _resolve_field_neighbors_from_redis(
            client, season, round_number, driver_id
        )
        if redis_result is not None:
            return redis_result
        return {
            "position": len(field) + 1,
            "gap_to_car_ahead": pit_predictor.MAX_GAP_SECONDS,
            "gap_to_car_behind": pit_predictor.MAX_GAP_SECONDS,
            "target_ahead_driver_id": None,
            "target_behind_driver_id": None,
        }

    driver_lap = field[index]
    driver_time = await _cumulative_race_time(db, session_id, driver_id, driver_lap.lap_number)

    gap_to_car_ahead = pit_predictor.MAX_GAP_SECONDS
    target_ahead_driver_id = None
    if index > 0:
        ahead = field[index - 1]
        ahead_time = await _cumulative_race_time(db, session_id, ahead.driver_id, ahead.lap_number)
        gap_to_car_ahead = min(max(driver_time - ahead_time, 0.0), pit_predictor.MAX_GAP_SECONDS)
        target_ahead_driver_id = ahead.driver_id

    gap_to_car_behind = pit_predictor.MAX_GAP_SECONDS
    target_behind_driver_id = None
    if index + 1 < len(field):
        behind = field[index + 1]
        behind_time = await _cumulative_race_time(
            db, session_id, behind.driver_id, behind.lap_number
        )
        gap_to_car_behind = min(max(behind_time - driver_time, 0.0), pit_predictor.MAX_GAP_SECONDS)
        target_behind_driver_id = behind.driver_id

    return {
        "position": driver_lap.position,
        "gap_to_car_ahead": gap_to_car_ahead,
        "gap_to_car_behind": gap_to_car_behind,
        "target_ahead_driver_id": target_ahead_driver_id,
        "target_behind_driver_id": target_behind_driver_id,
    }


def tire_deg_recommendation_contributions(
    models: dict[str, Any],
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None],
    driver_id: uuid.UUID,
    circuit_name: str,
    total_laps: int,
    pit_lap: int,
    recommended_compound: str,
) -> list[explainability.FeatureContribution]:
    """SHAP contributions for the RECOMMENDED stint (not the current one).

    Explains the first lap of the recommended stint (pit_lap + 1, tyre_age
    0 — the same convention build_pit_recommendation's own stint-2
    computation uses, see _stint2_batch_deltas) on recommended_compound's
    own pipeline. This is the correction Checkpoint 3 makes to what this
    explanation used to compute: previously it ran SHAP against the
    driver's CURRENT compound at the pit lap — a real, documented gap (see
    docs/core-feature-rebuild-strategy-recommendations.md §2a) where "even
    what it does explain isn't about the recommended stint."

    Public (no leading underscore): Checkpoint 4's prediction_worker.
    _compute_recommendation_fields calls this directly with its OWN loaded
    models/maps_cache, not strategy_service's — this is pure computation
    with no DB/cache dependency of its own, so which module's registry the
    caller passes in doesn't matter (see compute_pit_recommendation's own
    docstring for the same reasoning and why prediction_worker can't just
    call get_pit_window_with_explanation instead).

    Args:
        models: Loaded model registry, keyed by filename.
        maps_cache: Output of _load_encoding_maps().
        driver_id: Driver this recommendation is for.
        circuit_name: Circuit display name, for resolve_circuit_code.
        total_laps: Estimated race distance, for the fuel_adjusted_time feature.
        pit_lap: The #1 candidate's recommended pit lap.
        recommended_compound: The #1 candidate's recommended_compound.
    Returns:
        Top-k FeatureContribution list (see explainability.DEFAULT_TOP_K),
        or [] if recommended_compound has no loaded tire_deg pipeline (should
        not happen in practice — build_pit_recommendation already required
        this pipeline to produce recommended_compound in the first place —
        kept as a defensive fallback rather than an assumed invariant).
    """
    pipeline = _pipeline_for_compound(models, recommended_compound)
    if pipeline is None:
        return []

    maps = _encoding_maps_for_compound(maps_cache, recommended_compound)
    driver_code = tire_deg_model.resolve_driver_code(maps, str(driver_id))
    circuit_code = tire_deg_model.resolve_circuit_code(maps, circuit_name)
    stint2_first_lap = pit_lap + 1
    fuel_at_lap = tire_deg_model.ASSUMED_START_FUEL_KG * (1 - stint2_first_lap / max(total_laps, 1))
    fuel_adjusted_time = -tire_deg_model.FUEL_TIME_PENALTY_PER_KG * (
        tire_deg_model.ASSUMED_START_FUEL_KG - fuel_at_lap
    )
    features = np.array(
        [
            [
                stint2_first_lap,
                _COMPOUND_ENCODING.get(recommended_compound, _COMPOUND_ENCODING["MEDIUM"]),
                0,  # fresh tyre — first lap of the recommended stint
                fuel_adjusted_time,
                circuit_code,
                driver_code,
            ]
        ]
    )
    [contributions] = explainability.explain_prediction(
        pipeline, tire_deg_model.FEATURE_COLUMNS, features
    )
    return contributions


def pit_predictor_current_contributions(
    models: dict[str, Any],
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None],
    driver_id: uuid.UUID,
    state: dict[str, Any],
    neighbors: dict[str, Any],
) -> list[explainability.FeatureContribution]:
    """SHAP contributions for pit_predictor's read on the driver's CURRENT lap.

    Unlike the tire_deg explanation above (which explains a hypothetical
    future stint), this explains "why does the model think pitting is/isn't
    imminent right now" — using the driver's real current tyre age and REAL
    field position/rival gaps (via neighbors, from _resolve_field_neighbors).
    This is the rival-gap reasoning entirely absent from tire_deg_model.
    FEATURE_COLUMNS (see the core-feature-rebuild investigation's finding
    that the pre-Checkpoint-3 explanation "cannot express the vision's own
    example" — "gap to P4 behind is 8.2s").

    Public (no leading underscore): same cross-module reasoning as
    tire_deg_recommendation_contributions above — prediction_worker.
    _compute_recommendation_fields calls this directly, passing state/
    neighbors built from its OWN already-resolved per-lap context
    (_resolve_inference_context's output) rather than this module's
    _current_state/_resolve_field_neighbors, which would re-query the DB and
    race against process_lap's own commit of this same lap (see
    compute_pit_recommendation's docstring).

    Args:
        models: Loaded model registry, keyed by filename.
        maps_cache: Output of _load_encoding_maps() (or an equivalent cache).
        driver_id: Driver this recommendation is for.
        state: compound, tyre_age_laps, lap_number, total_laps, circuit_name
            — the same shape _current_state returns.
        neighbors: position, gap_to_car_ahead, gap_to_car_behind — the same
            shape _resolve_field_neighbors returns.
    Returns:
        Top-k FeatureContribution list, or [] if pit_predictor.pkl or the
        current compound's tire_deg pipeline (needed for
        predicted_life_remaining) isn't loaded.
    """
    pit_model = models.get("pit_predictor.pkl")
    current_pipeline = _pipeline_for_compound(models, state["compound"])
    if pit_model is None or current_pipeline is None:
        return []

    current_maps = _encoding_maps_for_compound(maps_cache, state["compound"])
    driver_code = tire_deg_model.resolve_driver_code(current_maps, str(driver_id))
    circuit_code = tire_deg_model.resolve_circuit_code(current_maps, state["circuit_name"])
    lap_number = state["lap_number"]
    total_laps = state["total_laps"]
    fuel_at_lap = tire_deg_model.ASSUMED_START_FUEL_KG * (1 - lap_number / max(total_laps, 1))
    fuel_adjusted_time = -tire_deg_model.FUEL_TIME_PENALTY_PER_KG * (
        tire_deg_model.ASSUMED_START_FUEL_KG - fuel_at_lap
    )
    predicted_life_remaining = float(
        tire_deg_model.predict_life_remaining_batch(
            current_pipeline,
            np.array([lap_number]),
            np.array([_COMPOUND_ENCODING.get(state["compound"], _COMPOUND_ENCODING["MEDIUM"])]),
            np.array([state["tyre_age_laps"]]),
            np.array([fuel_adjusted_time]),
            np.array([circuit_code]),
            np.array([driver_code]),
        )[0]
    )

    sc_model = models.get("safety_car_model.pkl")
    safety_car_probability = 0.0
    if sc_model is not None:
        safety_car_probability = sc_model.probability_within(
            state["circuit_name"], lap_number, state["compound"] in _WET_COMPOUNDS, 1
        )

    fuel_load_est = max(fuel_at_lap, 0.0)
    pit_features = np.array(
        [
            [
                state["tyre_age_laps"],
                predicted_life_remaining,
                neighbors["gap_to_car_ahead"],
                neighbors["gap_to_car_behind"],
                safety_car_probability,
                total_laps - lap_number,
                neighbors["position"],
                fuel_load_est,
            ]
        ]
    )
    [contributions] = explainability.explain_prediction(
        pit_model, pit_predictor.FEATURE_COLUMNS, pit_features
    )
    return contributions


def build_pit_recommendation_explanation(
    pit_lap: int,
    recommended_compound: str,
    confidence: float | None,
    tyre_age_laps: int,
    position: int,
    gap_to_car_ahead: float,
    target_ahead_driver_id: uuid.UUID | None,
    gap_to_car_behind: float,
    target_behind_driver_id: uuid.UUID | None,
    undercut_score: float | None,
    overcut_score: float | None,
    tire_deg_contributions: list[explainability.FeatureContribution],
    pit_predictor_contributions: list[explainability.FeatureContribution],
) -> PitRecommendationExplanation:
    """Combine tire_deg + pit_predictor SHAP and field/undercut context into
    structured facts and a plain-English narrative for one recommendation.

    Pure function — every input is already computed by the caller
    (get_pit_window_with_explanation, or prediction_worker.
    _compute_recommendation_fields — public/no leading underscore for this
    same cross-module reuse, same reasoning as
    tire_deg_recommendation_contributions above), which is what makes this
    testable without any DB/Redis/model fixtures.

    Args:
        pit_lap, recommended_compound, confidence: The #1 candidate's own
            fields (build_pit_recommendation).
        tyre_age_laps: The driver's real current tyre age (_current_state).
        position, gap_to_car_ahead, target_ahead_driver_id, gap_to_car_behind,
            target_behind_driver_id: Output of _resolve_field_neighbors.
        undercut_score: probability_pit_now_gains_position vs. the car ahead
            (get_undercut_score), or None if there's no car ahead or the
            required model wasn't loaded.
        overcut_score: probability_stay_out_retains_position vs. the car
            behind (get_overcut_score), or None for the same reasons.
        tire_deg_contributions: Output of tire_deg_recommendation_contributions.
        pit_predictor_contributions: Output of pit_predictor_current_contributions.
    Returns:
        PitRecommendationExplanation.
    """
    facts: list[ExplanationFact] = [
        ExplanationFact(label="Recommended pit lap", value=f"Lap {pit_lap}", source="tire_deg"),
        ExplanationFact(
            label="Recommended compound", value=recommended_compound, source="tire_deg"
        ),
        ExplanationFact(
            label="Current tyre age",
            value=f"{tyre_age_laps} lap{'s' if tyre_age_laps != 1 else ''}",
            source="field",
        ),
        ExplanationFact(label="Track position", value=f"P{position}", source="field"),
    ]
    if confidence is not None:
        facts.append(
            ExplanationFact(label="Confidence", value=f"{confidence * 100:.0f}%", source="tire_deg")
        )
    if target_ahead_driver_id is not None:
        facts.append(
            ExplanationFact(
                label="Gap to car ahead", value=f"{gap_to_car_ahead:.1f}s", source="pit_predictor"
            )
        )
    if target_behind_driver_id is not None:
        facts.append(
            ExplanationFact(
                label="Gap to car behind", value=f"{gap_to_car_behind:.1f}s", source="pit_predictor"
            )
        )
    if undercut_score is not None:
        facts.append(
            ExplanationFact(
                label="Undercut opportunity (car ahead)",
                value=f"{undercut_score * 100:.0f}% gain probability",
                source="pit_predictor",
            )
        )
    if overcut_score is not None:
        facts.append(
            ExplanationFact(
                label="Overcut risk (car behind)",
                value=f"{(1 - overcut_score) * 100:.0f}% they gain by pitting now",
                source="pit_predictor",
            )
        )
    for contribution in tire_deg_contributions[:1]:
        facts.append(
            ExplanationFact(
                label=explainability.FEATURE_LABELS.get(
                    contribution.feature_name, contribution.feature_name
                ),
                value=explainability.format_contribution(contribution, unit="s"),
                source="tire_deg",
            )
        )
    for contribution in pit_predictor_contributions[:1]:
        facts.append(
            ExplanationFact(
                label=explainability.FEATURE_LABELS.get(
                    contribution.feature_name, contribution.feature_name
                ),
                value=explainability.format_contribution(contribution, unit="probability"),
                source="pit_predictor",
            )
        )

    narrative = f"Lap {pit_lap} on {recommended_compound} is the recommended pit"
    if confidence is not None:
        narrative += f" ({confidence * 100:.0f}% confidence)"
    narrative += f". Tyre age is currently {tyre_age_laps} laps"
    if tire_deg_contributions:
        trend = "accelerating" if tire_deg_contributions[0].direction == "+" else "still manageable"
        narrative += f", degradation {trend}"
    narrative += "."

    if target_behind_driver_id is not None:
        safe = gap_to_car_behind > PIT_STOP_SECONDS
        narrative += f" Gap to the car behind is {gap_to_car_behind:.1f}s — " + (
            "safe to pit without losing the position."
            if safe
            else "a close call; rejoining could cost the position."
        )
    elif target_ahead_driver_id is None:
        narrative += " Currently the race leader — no car behind to defend against."

    if target_ahead_driver_id is not None and undercut_score is not None and undercut_score >= 0.5:
        narrative += (
            f" Pitting now also has a {undercut_score * 100:.0f}% chance of "
            "undercutting the car ahead."
        )

    return PitRecommendationExplanation(
        facts=facts,
        narrative=narrative,
        tire_deg_shap=[
            FeatureContributionResponse(
                feature_name=c.feature_name,
                value=c.value,
                contribution=c.contribution,
                direction=c.direction,
            )
            for c in tire_deg_contributions
        ],
        pit_predictor_shap=[
            FeatureContributionResponse(
                feature_name=c.feature_name,
                value=c.value,
                contribution=c.contribution,
                direction=c.direction,
            )
            for c in pit_predictor_contributions
        ],
    )


async def get_pit_window_with_explanation(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
) -> list[PitWindowResponse]:
    """Optimal pit window candidates plus a combined explanation for the top recommendation.

    Calls the cached build_pit_recommendation for the ranked candidates, then
    builds a combined explanation for the #1 candidate from THREE already-
    existing mechanisms, none of them cached here (cheap relative to
    build_pit_recommendation's own candidate search, which IS cached):

    - tire_deg SHAP against the RECOMMENDED stint (not the current one — see
      tire_deg_recommendation_contributions).
    - pit_predictor SHAP against the driver's CURRENT lap, using real field
      position and rival gaps (_resolve_field_neighbors) — the rival-gap
      reasoning tire_deg_model.FEATURE_COLUMNS structurally cannot express.
    - get_undercut_score/get_overcut_score against the real track-position
      neighbours resolved above — reused, not recomputed (both are
      themselves @cacheable).

    build_pit_recommendation_explanation combines all of the above into
    PitRecommendationExplanation.facts (structured) and .narrative
    (plain-English), alongside both raw SHAP arrays.

    Args:
        client: Redis client (cache-aside, forwarded to build_pit_recommendation
            and to get_undercut_score/get_overcut_score).
        db: Async DB session.
        season, round_number: Race weekend identifiers.
        session_id: Session to evaluate.
        driver_id: Driver to plan a pit window for.
    Returns:
        Up to 3 PitWindowResponse, ascending by projected_total_delta_seconds;
        only the first (recommended) candidate carries explanation — empty
        list if build_pit_recommendation itself returns no candidates.
    Raises:
        NotFoundError: No lap_data exists yet for this driver/session — HTTP
            404 at the API layer (see build_pit_recommendation's docstring).
        ModelNotLoadedError: No tire degradation model loaded for the
            driver's current compound.
    """
    candidates = await build_pit_recommendation(
        client, db, season, round_number, session_id, driver_id
    )
    responses = [PitWindowResponse(**candidate) for candidate in candidates]
    if not responses:
        return responses

    state = await _current_state(db, session_id, driver_id)
    models = _load_models()
    maps_cache = _load_encoding_maps()

    top = candidates[0]
    top_pit_lap = int(top["pit_lap"])
    recommended_compound = str(top["recommended_compound"])
    confidence = top["confidence_score"]

    tire_deg_contributions = tire_deg_recommendation_contributions(
        models,
        maps_cache,
        driver_id,
        state["circuit_name"],
        state["total_laps"],
        top_pit_lap,
        recommended_compound,
    )

    neighbors = await _resolve_field_neighbors(
        client, db, session_id, driver_id, state["lap_number"], season, round_number
    )
    pit_predictor_contributions = pit_predictor_current_contributions(
        models, maps_cache, driver_id, state, neighbors
    )

    undercut_score: float | None = None
    target_ahead_driver_id = neighbors["target_ahead_driver_id"]
    if target_ahead_driver_id is not None:
        try:
            undercut_result = await get_undercut_score(
                client, db, season, round_number, session_id, driver_id, target_ahead_driver_id
            )
            undercut_score = float(undercut_result["probability_pit_now_gains_position"])
        except ModelNotLoadedError:
            logger.warning(
                "get_pit_window_with_explanation: undercut score unavailable for driver %s",
                driver_id,
            )

    overcut_score: float | None = None
    target_behind_driver_id = neighbors["target_behind_driver_id"]
    if target_behind_driver_id is not None:
        try:
            overcut_result = await get_overcut_score(
                client, db, season, round_number, session_id, driver_id, target_behind_driver_id
            )
            overcut_score = float(overcut_result["probability_stay_out_retains_position"])
        except ModelNotLoadedError:
            logger.warning(
                "get_pit_window_with_explanation: overcut score unavailable for driver %s",
                driver_id,
            )

    explanation = build_pit_recommendation_explanation(
        pit_lap=top_pit_lap,
        recommended_compound=recommended_compound,
        confidence=confidence,
        tyre_age_laps=state["tyre_age_laps"],
        position=neighbors["position"],
        gap_to_car_ahead=neighbors["gap_to_car_ahead"],
        target_ahead_driver_id=target_ahead_driver_id,
        gap_to_car_behind=neighbors["gap_to_car_behind"],
        target_behind_driver_id=target_behind_driver_id,
        undercut_score=undercut_score,
        overcut_score=overcut_score,
        tire_deg_contributions=tire_deg_contributions,
        pit_predictor_contributions=pit_predictor_contributions,
    )
    responses[0] = responses[0].model_copy(update={"explanation": explanation})
    return responses


# --- get_undercut_score / get_overcut_score ---


async def _undercut_overcut_probability(
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    pitting_now_driver_id: uuid.UUID,
    pitting_next_lap_driver_id: uuid.UUID,
) -> dict[str, Any]:
    """Shared projection backing get_undercut_score/get_overcut_score.

    pitting_now_driver_id pits this lap and runs the full projection window on a
    fresh tyre; pitting_next_lap_driver_id stays out one more lap on its current
    (aging) tyre, then pits and runs the remaining window on a fresh tyre (compound
    unchanged after a pit stop, matching race_simulator.py's assumption).
    UNDERCUT_MONTE_CARLO_SIMS Gaussian-noise draws turn the deterministic tire_deg
    prediction into a probability that pitting_now_driver_id ends up ahead.

    Args:
        db: Async DB session.
        season, round_number: Race weekend identifiers (unused now that the
            track_temp/air_temp-driven weather lookup has been removed from
            the feature vector — see tire_deg_model.FEATURE_COLUMNS — kept as
            parameters since callers resolve them anyway and a future
            weather-aware retrain will need them again).
        session_id: Session to evaluate.
        pitting_now_driver_id: Driver assumed to pit this lap.
        pitting_next_lap_driver_id: Driver assumed to pit next lap.
    Returns:
        Dict with probability_pit_now_gains_position, projected_gap_seconds (mean
        over sims; positive = pitting_now_driver_id ends up ahead), n_laps_projected.
    """
    models = _load_models()
    maps_cache = _load_encoding_maps()
    now_state = await _current_state(db, session_id, pitting_now_driver_id)
    next_state = await _current_state(db, session_id, pitting_next_lap_driver_id)

    now_time = await _cumulative_race_time(
        db, session_id, pitting_now_driver_id, now_state["lap_number"]
    )
    next_time = await _cumulative_race_time(
        db, session_id, pitting_next_lap_driver_id, next_state["lap_number"]
    )
    # Positive deficit => pitting_now_driver_id currently trails pitting_next_lap_driver_id.
    deficit = now_time - next_time

    now_pipeline = _pipeline_for_compound(models, now_state["compound"])
    next_pipeline = _pipeline_for_compound(models, next_state["compound"])
    if now_pipeline is None or next_pipeline is None:
        raise ModelNotLoadedError("Required tire degradation model not loaded")

    # Resolved per-driver against THEIR OWN compound's map — now_pipeline and
    # next_pipeline can be different models entirely (different compound, and
    # possibly a different training run's code universe under item 9's
    # per-compound promotion), so now_code/now_circuit_code must never be fed
    # into next_pipeline or vice versa. next_code/next_circuit_code are reused
    # for both the stay_out AND fresh segments below, since both run on
    # next_pipeline (compound unchanged after a pit stop, per this function's
    # own docstring) — one resolution covers both.
    now_maps = _encoding_maps_for_compound(maps_cache, now_state["compound"])
    next_maps = _encoding_maps_for_compound(maps_cache, next_state["compound"])
    now_code = tire_deg_model.resolve_driver_code(now_maps, str(pitting_now_driver_id))
    next_code = tire_deg_model.resolve_driver_code(next_maps, str(pitting_next_lap_driver_id))
    now_circuit_code = tire_deg_model.resolve_circuit_code(now_maps, now_state["circuit_name"])
    next_circuit_code = tire_deg_model.resolve_circuit_code(next_maps, next_state["circuit_name"])
    default_compound_code = _COMPOUND_ENCODING["MEDIUM"]
    now_compound_encoded = _COMPOUND_ENCODING.get(now_state["compound"], default_compound_code)
    next_compound_encoded = _COMPOUND_ENCODING.get(next_state["compound"], default_compound_code)

    rng = np.random.default_rng()

    # The deterministic tire_deg projection for each of the three stint
    # segments (now/stay_out/fresh) is the SAME on every one of the
    # UNDERCUT_MONTE_CARLO_SIMS draws — none of _project_stint_delta's inputs
    # vary with the simulation index, only the noise term does (see
    # _sampled_noise). The original implementation called pipeline.predict()
    # inside the loop (3 calls/iteration x 200 iterations = 600 calls/
    # invocation, ~36s measured) recomputing an identical single-row
    # prediction every time. Projecting each segment once (3 total predict()
    # calls) and vectorizing only the noise draws across all 200 simulations
    # is mathematically equivalent — same deterministic term, same noise
    # distribution per sample, just not needlessly recomputed — and measured
    # at <1s. See CLAUDE.md's Deferred Wiring entry for before/after timing.
    now_deterministic = _project_stint_delta(
        now_pipeline,
        now_compound_encoded,
        now_code,
        now_circuit_code,
        now_state["lap_number"] + 1,
        UNDERCUT_PROJECTION_LAPS,
        0,
        now_state["total_laps"],
    )
    stay_out_deterministic = _project_stint_delta(
        next_pipeline,
        next_compound_encoded,
        next_code,
        next_circuit_code,
        next_state["lap_number"] + 1,
        1,
        next_state["tyre_age_laps"],
        next_state["total_laps"],
    )
    fresh_deterministic = _project_stint_delta(
        next_pipeline,
        next_compound_encoded,
        next_code,
        next_circuit_code,
        next_state["lap_number"] + 2,
        UNDERCUT_PROJECTION_LAPS - 1,
        0,
        next_state["total_laps"],
    )

    now_delta = (
        PIT_STOP_SECONDS
        + now_deterministic
        + _sampled_noise(rng, UNDERCUT_PROJECTION_LAPS, UNDERCUT_MONTE_CARLO_SIMS)
    )
    stay_out_delta = stay_out_deterministic + _sampled_noise(rng, 1, UNDERCUT_MONTE_CARLO_SIMS)
    fresh_delta = (
        PIT_STOP_SECONDS
        + fresh_deterministic
        + _sampled_noise(rng, UNDERCUT_PROJECTION_LAPS - 1, UNDERCUT_MONTE_CARLO_SIMS)
    )
    next_delta = stay_out_delta + fresh_delta

    new_deficit = deficit + now_delta - next_delta
    gap_samples = -new_deficit
    wins = int(np.sum(new_deficit < 0))

    return {
        "probability_pit_now_gains_position": wins / UNDERCUT_MONTE_CARLO_SIMS,
        "projected_gap_seconds": float(gap_samples.mean()),
        "n_laps_projected": UNDERCUT_PROJECTION_LAPS,
    }


def _key_undercut(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    target_driver_id: uuid.UUID,
) -> str:
    return f"f1:{season}:{round_number}:strategy:{driver_id}:undercut:{target_driver_id}"


@cacheable(ttl=30, key_fn=_key_undercut)
async def get_undercut_score(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    target_driver_id: uuid.UUID,
) -> dict[str, Any]:
    """Probability driver_id gains track position by pitting now vs. target pitting next lap.

    Args:
        client: Redis client (cache-aside).
        db: Async DB session.
        season, round_number: Race weekend identifiers, for the cache key.
        session_id: Session to evaluate.
        driver_id: The driver considering an undercut.
        target_driver_id: The rival being undercut.
    Returns:
        See _undercut_overcut_probability, plus target_driver_id and
        recommended_action ("PIT NOW" if probability_pit_now_gains_position
        >= 0.5, else "STAY OUT").
    """
    result = await _undercut_overcut_probability(
        db, season, round_number, session_id, driver_id, target_driver_id
    )
    recommended_action = (
        "PIT NOW" if result["probability_pit_now_gains_position"] >= 0.5 else "STAY OUT"
    )
    return {
        "target_driver_id": str(target_driver_id),
        "recommended_action": recommended_action,
        **result,
    }


def _key_overcut(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    target_driver_id: uuid.UUID,
) -> str:
    return f"f1:{season}:{round_number}:strategy:{driver_id}:overcut:{target_driver_id}"


@cacheable(ttl=30, key_fn=_key_overcut)
async def get_overcut_score(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    target_driver_id: uuid.UUID,
) -> dict[str, Any]:
    """Probability driver_id retains/gains track position by staying out while target pits now.

    Mirrors get_undercut_score with the pit timing reversed: target_driver_id is
    the one pitting "now" and driver_id is the one staying out an extra lap, so
    the shared helper's probability_pit_now_gains_position (which describes the
    *pitting* driver's win chance) is inverted back to driver_id's perspective.

    Args: same as get_undercut_score.
    Returns:
        Dict with target_driver_id, probability_stay_out_retains_position,
        projected_gap_seconds (driver_id's perspective), n_laps_projected.
    """
    result = await _undercut_overcut_probability(
        db, season, round_number, session_id, target_driver_id, driver_id
    )
    return {
        "target_driver_id": str(target_driver_id),
        "probability_stay_out_retains_position": 1.0 - result["probability_pit_now_gains_position"],
        "projected_gap_seconds": -result["projected_gap_seconds"],
        "n_laps_projected": result["n_laps_projected"],
    }


# --- get_competitor_predicted_strategy ---


def _first_pit_laps_over_threshold_batch(
    pit_model: Any,
    models: dict[str, Any],
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None],
    driver_ids: list[str],
    compounds: list[str],
    circuit_name: str,
    current_laps: np.ndarray,
    tyre_ages: np.ndarray,
    positions: np.ndarray,
    gaps_to_ahead: np.ndarray,
    gaps_to_behind: np.ndarray,
    safety_car_probabilities: np.ndarray,
    total_laps: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Roll pit_predictor forward lap-by-lap, for every driver at once, until each
    crosses pit_predictor.ALERT_THRESHOLD.

    Same per-driver math as the single-driver version this replaces, just
    vectorised across drivers at each lap offset instead of looping
    driver-then-offset: with ~20 drivers and up to COMPETITOR_STRATEGY_HORIZON_LAPS
    offsets, the old one-row-per-call approach made up to 300 separate
    pit_model.predict_proba/tire_deg predict_life_remaining_batch calls, each
    paying fixed per-call model overhead — the confirmed dominant cost behind
    get_competitor_predicted_strategy's 16-17s cold-compute floor (see
    CLAUDE.md's Deferred Wiring). This makes at most COMPETITOR_STRATEGY_HORIZON_LAPS
    batched pit_model calls (fewer once some drivers cross the threshold and
    drop out of the active set), further grouped by compound for the tire_deg
    life-remaining sub-call — a different pipeline object per compound, so
    those can't be merged across compounds the way pit_model's single unified
    model can be.

    driver_ids/circuit_name (rather than pre-resolved codes) are taken raw and
    resolved to codes INSIDE the per-compound-group loop below, against that
    group's own compound's encoding maps — different drivers in the same
    session can be on different compounds whose tire_deg models were promoted
    from different training runs (item 9's per-compound promotion), so there
    is no single "the" driver/circuit code for the whole batch; it depends on
    which pipeline a given group's life-remaining call is about to use.

    gap_to_ahead/behind and safety_car_probability are held constant at the
    caller-supplied values — see module docstring for why no forward gap/SC
    model is available here.

    Args: one entry per driver (arrays/list all the same length n, in the
        same order) except maps_cache/circuit_name (shared context); see
        pit_predictor.FEATURE_COLUMNS for feature semantics.
    Returns:
        (predicted_pit_lap, pit_probability_at_that_lap) arrays, one entry
        per driver. For any driver who never crosses the threshold within
        their horizon, holds that driver's horizon-final lap and probability
        — same fallback as the single-driver version.
    """
    n = len(current_laps)
    horizon = np.minimum(COMPETITOR_STRATEGY_HORIZON_LAPS, np.maximum(total_laps - current_laps, 1))
    last_lap = current_laps.copy()
    last_prob = np.zeros(n, dtype=np.float64)
    crossed = np.zeros(n, dtype=bool)
    compound_encoded = np.array(
        [_COMPOUND_ENCODING.get(c, _COMPOUND_ENCODING["MEDIUM"]) for c in compounds]
    )

    max_horizon = int(horizon.max()) if n else 0
    for offset in range(1, max_horizon + 1):
        active = (~crossed) & (offset <= horizon)
        if not active.any():
            continue
        idx = np.nonzero(active)[0]
        lap_number = current_laps[idx] + offset
        future_tyre_age = tyre_ages[idx] + offset
        fuel_load_est = np.clip(
            tire_deg_model.ASSUMED_START_FUEL_KG * (1 - lap_number / max(total_laps, 1)), 0.0, None
        )

        life_remaining = np.full(len(idx), float(tire_deg_model.MAX_LOOKAHEAD_LAPS))
        for compound in {compounds[i] for i in idx}:
            pipeline = _pipeline_for_compound(models, compound)
            if pipeline is None:
                continue
            group_mask = np.array([compounds[i] == compound for i in idx])
            group_idx = idx[group_mask]
            group_maps = _encoding_maps_for_compound(maps_cache, compound)
            group_driver_codes = np.array(
                [tire_deg_model.resolve_driver_code(group_maps, driver_ids[i]) for i in group_idx]
            )
            group_circuit_code = tire_deg_model.resolve_circuit_code(group_maps, circuit_name)
            life_remaining[group_mask] = tire_deg_model.predict_life_remaining_batch(
                pipeline,
                current_laps[group_idx] + offset,
                compound_encoded[group_idx],
                tyre_ages[group_idx] + offset,
                np.zeros(len(group_idx)),
                np.full(len(group_idx), group_circuit_code),
                group_driver_codes,
            )

        features = np.column_stack(
            [
                future_tyre_age,
                life_remaining,
                gaps_to_ahead[idx],
                gaps_to_behind[idx],
                safety_car_probabilities[idx],
                total_laps - lap_number,
                positions[idx],
                fuel_load_est,
            ]
        )
        probabilities = np.asarray(pit_model.predict_proba(features))[:, 1]

        last_lap[idx] = lap_number
        last_prob[idx] = probabilities
        crossed[idx[probabilities >= pit_predictor.ALERT_THRESHOLD]] = True

    return last_lap, last_prob


def _key_competitor_strategy(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
) -> str:
    return f"f1:{season}:{round_number}:strategy:competitors"


@cacheable(ttl=30, key_fn=_key_competitor_strategy)
async def get_competitor_predicted_strategy(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """For every driver in a session, estimate their most likely upcoming pit lap.

    Args:
        client: Redis client (cache-aside).
        db: Async DB session.
        season, round_number: Race weekend identifiers, for the cache key.
        session_id: Session to evaluate.
    Returns:
        One dict per driver: driver_id, predicted_pit_lap, pit_probability.
    """
    models = _load_models()
    maps_cache = _load_encoding_maps()
    pit_model = models.get("pit_predictor.pkl")
    if pit_model is None:
        raise ModelNotLoadedError("pit_predictor model not loaded")

    subq = (
        select(LapData.driver_id, func.max(LapData.lap_number).label("max_lap"))
        .where(LapData.session_id == session_id)
        .group_by(LapData.driver_id)
        .subquery()
    )
    join_condition = (LapData.driver_id == subq.c.driver_id) & (
        LapData.lap_number == subq.c.max_lap
    )
    query = select(LapData).join(subq, join_condition).where(LapData.session_id == session_id)
    latest_laps = list((await db.execute(query)).scalars().all())
    if not latest_laps:
        return []

    total_laps = max(lap.lap_number for lap in latest_laps)
    circuit_query = (
        select(Race.circuit_id, Circuit.name)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .join(Circuit, Race.circuit_id == Circuit.id)
        .where(SessionModel.id == session_id)
    )
    _circuit_id, circuit_name = (await db.execute(circuit_query)).one()

    driver_ids = [str(lap.driver_id) for lap in latest_laps]
    compounds = [lap.compound for lap in latest_laps]
    n = len(latest_laps)
    predicted_laps, probabilities = _first_pit_laps_over_threshold_batch(
        pit_model,
        models,
        maps_cache,
        driver_ids,
        compounds,
        circuit_name,
        np.array([lap.lap_number for lap in latest_laps]),
        np.array([lap.tyre_age_laps for lap in latest_laps]),
        np.array([lap.position or n for lap in latest_laps]),
        np.full(n, pit_predictor.MAX_GAP_SECONDS),
        np.full(n, pit_predictor.MAX_GAP_SECONDS),
        np.zeros(n),
        total_laps,
    )
    return [
        {
            "driver_id": driver_ids[i],
            "predicted_pit_lap": int(predicted_laps[i]),
            "pit_probability": float(probabilities[i]),
        }
        for i in range(n)
    ]


# --- get_strategy_prediction_history ---


async def get_strategy_prediction_history(
    db: AsyncSession, session_id: uuid.UUID, driver_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Full StrategyPrediction history for one driver in a session, oldest first.

    Supplementary to get_strategy_overview_for_session (which stays live/
    current, one row per driver, cached) — this is every persisted prediction
    for a single driver, for viewing progression over time. Not cached: a
    cache-aside TTL would show a stale (non-growing) list during exactly the
    live-progression use case this endpoint exists for, and it's a plain
    indexed read (ix_strategy_predictions_session_driver_lap_number), not an
    ML computation like the endpoints that do use @cacheable.

    Args:
        db: Async DB session.
        session_id: Session to read.
        driver_id: Driver whose prediction history to return.
    Returns:
        One dict per StrategyPrediction row (lap_number, predicted_pit_lap,
        pit_probability, undercut_score, overcut_score, created_at, plus the
        Checkpoint 4 recommendation-engine fields: recommended_pit_lap,
        window_start, window_end, recommended_compound, confidence_score,
        explanation — None/0.0/None on a row predating that migration or
        where the computation degraded gracefully that lap), ordered by
        lap_number ascending with NULLS LAST (rows predicted before the
        2026-08-26 lap_number migration have no lap_number and sort after
        every row that does), predicted_at ascending as the tiebreak. Empty
        list if this driver has no predictions yet in this session.
    """
    query = (
        select(StrategyPrediction)
        .where(
            StrategyPrediction.session_id == session_id,
            StrategyPrediction.driver_id == driver_id,
        )
        .order_by(
            StrategyPrediction.lap_number.asc().nulls_last(),
            StrategyPrediction.predicted_at.asc(),
        )
    )
    rows = (await db.execute(query)).scalars().all()
    return [
        {
            "lap_number": row.lap_number,
            "predicted_pit_lap": row.optimal_pit_lap,
            "pit_probability": row.pit_probability,
            "undercut_score": row.undercut_score,
            "overcut_score": row.overcut_score,
            "created_at": row.created_at,
            "recommended_pit_lap": row.recommended_pit_lap,
            "window_start": row.window_start,
            "window_end": row.window_end,
            "recommended_compound": row.recommended_compound,
            "confidence_score": row.confidence_score,
            "explanation": row.explanation,
        }
        for row in rows
    ]


# --- get_last_ingested_session ---


def _key_last_ingested_session(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
) -> str:
    return "f1:strategy:last_ingested_session"


@cacheable(ttl=LAST_INGESTED_SESSION_TTL_SECONDS, key_fn=_key_last_ingested_session)
async def _fetch_last_ingested_session(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
) -> dict[str, Any]:
    """Newest-race_date COMPLETED R session that has ingested lap data.

    Race.status == "completed" (quick mitigation for the B1 finding in
    docs/simulator-issues-wet-model-and-position-context.md, not a fix for
    the underlying CLAUDE.md Deferred Wiring item A): without this filter,
    the picker could resolve to a partially live-ingested session whose
    lap_data has NULL position and unevenly-missing laps (e.g. Dutch GP 2026
    Round 12, ingested by a Day 36 live dry run, status="scheduled") — that
    produces nonsensical Strategy Simulator output (garbage starting_position
    fallback, non-comparable cumulative race times), not a real ingestion
    fault. A real ingest_historical.py run always sets status="completed".

    Args:
        client: Redis client (cache-aside — first positional arg per cacheable's contract).
        db: Async DB session.
    Returns:
        JSON-serialisable dict: session_id, season, round_number, event_name
        (nullable), circuit_name, race_date (ISO string).
    Raises:
        NotFoundError: No completed R session with any lap_data exists — a
            fresh DB, or one with only in-progress/scheduled ingestion so
            far; @cacheable does not cache the raised result, so this
            re-queries until data lands.
    """
    query = (
        select(
            SessionModel.id,
            Race.season,
            Race.round_number,
            Race.event_name,
            Circuit.name,
            Race.race_date,
        )
        .join(Race, SessionModel.race_id == Race.id)
        .join(Circuit, Race.circuit_id == Circuit.id)
        .where(
            SessionModel.session_type == "R",
            Race.status == "completed",
            select(LapData.id).where(LapData.session_id == SessionModel.id).exists(),
        )
        .order_by(Race.race_date.desc(), Race.season.desc(), Race.round_number.desc())
        .limit(1)
    )
    row = (await db.execute(query)).one_or_none()
    if row is None:
        raise NotFoundError("No ingested race sessions available")
    return {
        "session_id": str(row[0]),
        "season": int(row[1]),
        "round_number": int(row[2]),
        "event_name": row[3],
        "circuit_name": row[4],
        "race_date": row[5].isoformat(),
    }


async def get_last_ingested_session(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
) -> LastIngestedSessionResponse:
    """The R session with the newest race_date that has ingested lap data.

    Backs GET /strategy/last-ingested-session — the Strategy Simulator's
    session source when no race is live. Resolved per-environment from that
    environment's own DB, so it always points at something valid regardless
    of which DB the backend is running against.

    Args:
        client: Redis client (cache-aside, forwarded to _fetch_last_ingested_session).
        db: Async DB session.
    Returns:
        LastIngestedSessionResponse.
    Raises:
        NotFoundError: No R session with any lap_data exists (fresh DB).
    """
    data = await _fetch_last_ingested_session(client, db)
    return LastIngestedSessionResponse.model_validate(data)


# --- Session-scoped wrappers (route-facing: resolve season/round, then delegate) ---


async def get_pit_window_for_session(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
) -> list[PitWindowResponse]:
    """Resolve season/round for a session, then delegate to get_pit_window_with_explanation.

    Args: see get_pit_window_with_explanation — season/round_number are
        resolved here rather than caller-supplied.
    Returns:
        See get_pit_window_with_explanation.
    Raises:
        NotFoundError: No session, or no lap_data yet for this driver/session.
        ModelNotLoadedError: No tire degradation model loaded for the compound.
    """
    season, round_number = await resolve_season_round(db, session_id)
    return await get_pit_window_with_explanation(
        client, db, season, round_number, session_id, driver_id
    )


async def get_undercut_for_session(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    target_driver_id: uuid.UUID,
) -> UndercutThreatResponse:
    """Resolve season/round for a session, then delegate to get_undercut_score.

    Args: see get_undercut_score — season/round_number are resolved here
        rather than caller-supplied.
    Returns:
        UndercutThreatResponse wrapping get_undercut_score's result.
    Raises:
        NotFoundError: No session, or no lap_data yet for either driver.
        ModelNotLoadedError: Required tire degradation model not loaded.
    """
    season, round_number = await resolve_season_round(db, session_id)
    result = await get_undercut_score(
        client, db, season, round_number, session_id, driver_id, target_driver_id
    )
    return UndercutThreatResponse.model_validate(result)


async def get_strategy_overview_for_session(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    session_id: uuid.UUID,
) -> StrategyOverviewResponse:
    """Resolve season/round for a session, then delegate to get_competitor_predicted_strategy.

    Args: see get_competitor_predicted_strategy — season/round_number are
        resolved here rather than caller-supplied.
    Returns:
        StrategyOverviewResponse — every driver's predicted strategy, for the
        team strategy wall view.
    Raises:
        NotFoundError: No session with this ID exists.
        ModelNotLoadedError: pit_predictor model not loaded.
    """
    season, round_number = await resolve_season_round(db, session_id)
    drivers = await get_competitor_predicted_strategy(client, db, season, round_number, session_id)
    return StrategyOverviewResponse(
        session_id=session_id,
        drivers=[CompetitorStrategyEntry.model_validate(d) for d in drivers],
    )


async def get_strategy_prediction_history_for_session(
    db: AsyncSession, session_id: uuid.UUID, driver_id: uuid.UUID
) -> StrategyPredictionHistoryResponse:
    """Shape get_strategy_prediction_history's rows into the route's response schema.

    No season/round resolution needed here (unlike the other session-scoped
    wrappers above) — get_strategy_prediction_history queries by session_id/
    driver_id directly, it isn't cache-aside keyed by season/round.

    Args:
        db: Async DB session.
        session_id: Session to read.
        driver_id: Driver whose prediction history to return.
    Returns:
        StrategyPredictionHistoryResponse — empty predictions list if this
        driver has no persisted predictions yet in this session (not a 404;
        an empty history is a valid, expected state for a driver with no
        laps processed yet).
    """
    history = await get_strategy_prediction_history(db, session_id, driver_id)
    return StrategyPredictionHistoryResponse(
        session_id=session_id,
        driver_id=driver_id,
        predictions=[StrategyPredictionHistoryEntry.model_validate(entry) for entry in history],
    )
