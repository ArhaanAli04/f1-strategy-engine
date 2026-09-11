"""Celery task that runs the strategy ML models and persists + publishes predictions."""

import asyncio
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

import boto3
import joblib
import numpy as np
import redis
import redis.asyncio as aioredis
import sentry_sdk
from botocore.exceptions import ClientError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_aws_settings, get_ml_settings, get_redis_settings
from backend.core.database import get_engine
from backend.core.exceptions import ModelNotLoadedError
from backend.core.metrics import (
    f1_ml_inference_duration_seconds,
    f1_strategy_predictions_total,
)
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData
from backend.services import alert_service, strategy_service

# pit_predictor uses the redundant "as X" alias, not a plain import — tests
# reach it via prediction_worker.pit_predictor (see strategy_service.py's
# identical note for why); race_simulator/tire_deg_model have no such
# external access pattern today.
from backend.services.ml import pit_predictor as pit_predictor
from backend.services.ml import race_simulator, tire_deg_model
from backend.services.ml.race_simulator import DriverRaceState, RaceSimulationInput
from backend.workers.celery_app import app

logger = logging.getLogger(__name__)

# Per the ML Model Registry in CLAUDE.md.
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

# Same encoding convention as strategy_service.py's identical constant — kept
# duplicated here (used inside the synchronous _run_inference) rather than
# imported, independent of the strategy_service import below used for the
# undercut/overcut calls, which do need a real cross-module call.
_COMPOUND_ENCODING = {"HARD": 0, "INTERMEDIATE": 1, "MEDIUM": 2, "SOFT": 3, "WET": 4}
_WET_COMPOUNDS = frozenset({"INTERMEDIATE", "WET"})

# Simplified fresh-vs-worn-tyre pace-recovery estimate for the Simulator UI's
# plan explanation panel — NOT derived from the tire_deg model's own predicted
# delta (that's computed inside race_simulator.simulate_race's internal
# per-lap loop and never returned to the caller). INTERMEDIATE/WET default to
# 0.0 — no dry-tyre degradation-recovery assumption applies to them.
_FRESH_TYRE_GAIN_PER_LAP_SECONDS = {"HARD": 0.3, "MEDIUM": 0.5, "SOFT": 0.8}

_model_cache: dict[str, Any] = {}
# Per tire_deg model filename, its own CategoricalEncodingMaps (or None if that
# model's sidecar is missing/legacy — predates the encoding-persistence fix).
# Populated as a side effect of _load_models(), same process lifetime as
# _model_cache — see _load_encoding_maps() and tire_deg_model.py's "Training-
# time categorical encoding" section.
_encoding_maps_cache: dict[str, Any] = {}
# Per tire_deg model filename, its own recovered holdout_mae (or None if that
# model's sidecar is missing/legacy) — populated alongside _encoding_maps_cache
# as a side effect of _load_models(), same reasoning. Duplicated from
# strategy_service.py's identical cache (same no-cross-service-import
# convention as this module's other duplicated helpers) — needed here as of
# Checkpoint 4 so _compute_recommendation_fields can call strategy_service.
# compute_pit_recommendation's confidence Monte Carlo with THIS process's own
# loaded models, not strategy_service's separate cache.
_holdout_mae_cache: dict[str, float | None] = {}
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def _local_model_path(filename: str) -> Path:
    model_dir = Path(get_ml_settings().model_cache_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir / filename


def _download_from_s3(filename: str) -> Path:
    """Download a model file from S3, always fresh for this process.

    Deliberately does NOT check whether path already exists on disk and skip
    the download if so — that was the original behavior (see git history) and
    it silently served stale models forever, since the local file never
    expires and nothing else ever invalidates it. Confirmed as a real bug
    2026-09-11 (docs/tire-deg-model-quality-and-rival-pit-behavior.md's CP3):
    both the host machine's and the running worker container's cache
    directories held .pkl files dated weeks/a session earlier than the
    latest real promotion, so "docker compose restart worker" — this
    project's own documented convention for picking up a newly-promoted
    model — never actually worked, because a plain restart doesn't touch the
    container's writable filesystem layer where this cache lived.

    This still costs only ONE fetch per process (not per call): _load_models
    only ever calls this once per filename, guarded by the module-level
    _model_cache dict, so the per-process "load once, cache in memory for the
    process's lifetime" contract CLAUDE.md documents is unchanged — only the
    now-removed disk layer surviving PROCESS RESTARTS is gone. These files
    are ~1MB each (7 total); against this project's own documented ~88s
    cold-start import time (xgboost/lightgbm/shap), a few extra MB over the
    network at startup is noise.

    Args:
        filename: Model file name, as listed in the ML Model Registry.
    Returns:
        Local filesystem path to the freshly-downloaded file.
    """
    path = _local_model_path(filename)
    settings = get_aws_settings()
    client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    key = f"{_MODEL_VERSION_TAG}/{filename}"
    client.download_file(settings.aws_bucket_name, key, str(path))
    return path


def _local_metrics_path(filename: str) -> Path:
    model_dir = Path(get_ml_settings().model_cache_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir / f"{filename}.metrics.json"


def _download_metrics_from_s3(filename: str) -> dict[str, Any] | None:
    """Download a tire_deg model's own sidecar metrics.json from S3, always fresh for this process.

    Duplicated from strategy_service.py's identical helper — same no-cross-service-
    import convention as this module's other duplicated helpers (_resolve_weather,
    _encoding_maps_for_compound). Same always-fresh-per-process contract as
    _download_from_s3 (see that function's docstring for why the old
    skip-if-cached-on-disk behavior was a real staleness bug, not a valid
    optimization) — still only one fetch per filename per process, via
    _load_models' own once-per-process guard, not a fetch per call.

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
    model's own recovered holdout_mae (see strategy_service.compute_pit_
    recommendation's confidence computation, called from this module's own
    _compute_recommendation_fields as of Checkpoint 4) — same once-per-process
    lifecycle as the models themselves, so this function is the single place
    all three caches get populated together, which is what lets
    apply_incompatible_model_fallbacks alias all three in lockstep below.

    Args:
        None.
    Returns:
        Mapping of model filename to the deserialised model object.
    """
    if _model_cache:
        return _model_cache
    for filename in _MODEL_FILES:
        path = _download_from_s3(filename)
        _model_cache[filename] = joblib.load(path)
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
        filename whose sidecar is missing/legacy.
    """
    _load_models()
    return _holdout_mae_cache


def _encoding_maps_for_compound(
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None], compound: str
) -> tire_deg_model.CategoricalEncodingMaps | None:
    """Look up the tire_deg encoding maps for a compound, defaulting to MEDIUM's suffix.

    Duplicated from strategy_service.py's identical helper (same no-cross-service-
    import convention as this module's other duplicated helpers). Mirrors the
    compound -> filename suffix lookup already inlined at each of this module's
    pipeline-selection call sites — a driver/circuit code must always be resolved
    against the SAME model's own map as the pipeline it's about to be fed into.

    Args:
        maps_cache: Output of _load_encoding_maps().
        compound: Tyre compound name.
    Returns:
        That compound's CategoricalEncodingMaps, or None if unavailable.
    """
    suffix = _COMPOUND_TO_MODEL_SUFFIX.get(compound, "medium")
    return maps_cache.get(f"tire_deg_{suffix}.pkl")


def _weather_key(season: int, round_number: int) -> str:
    return f"f1:{season}:{round_number}:weather:latest"


async def _resolve_weather(
    async_redis_client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    circuit_id: uuid.UUID,
    compound: str,
) -> tuple[float, float]:
    """Current track_temp/air_temp for a tire_deg inference feature vector.

    Duplicated from strategy_service._resolve_weather — identical contract
    (live f1:{season}:{round}:weather:latest key first, DB circuit+compound
    average as fallback). Duplicated rather than imported for the same
    no-cross-service-import reason as this module's other duplicated helpers.

    Args:
        async_redis_client: Async Redis client.
        db: Async DB session.
        season, round_number: Race weekend identifiers.
        circuit_id: Circuit to average over on fallback.
        compound: Compound to average over on fallback.
    Returns:
        (track_temp, air_temp) in Celsius.
    """
    raw = await async_redis_client.get(_weather_key(season, round_number))
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


async def _resolve_position_context_from_redis(
    async_redis_client: aioredis.Redis,  # type: ignore[type-arg]
    season: int,
    round_number: int,
    driver_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Field position/gaps from the live-authoritative f1:{season}:{round}:gaps key.

    Fallback for a driver whose lap_data has no usable position within the
    _resolve_position_context bound (see that function's own docstring for
    when this is reached) — reads the same key ingest_live_session.py's
    _publish_live_gaps and replay_pipeline.py's own gaps-publish both write
    (CLAUDE.md's Redis Cache Key Schema): SessionGapsResponse-shaped JSON,
    entries already sorted by position with gap_to_ahead_seconds/
    gap_to_behind_seconds populated per entry directly from F1's own feed (or
    FastF1's Time column for a replay) — not a DB reconstruction, so this is
    at least as authoritative as the DB path it's backstopping, not merely a
    degraded substitute.

    Args:
        async_redis_client: Async Redis client.
        season, round_number: Race weekend identifiers, for the key.
        driver_id: Driver to locate within the field.
    Returns:
        Same shape as _resolve_position_context's return value, or None if
        the key is missing/unparsable/this driver isn't in it — callers must
        treat None as "no live-gaps fallback available," not an error.
    """
    raw = await async_redis_client.get(f"f1:{season}:{round_number}:gaps")
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


async def _resolve_position_context(
    db: AsyncSession,
    async_redis_client: aioredis.Redis,  # type: ignore[type-arg]
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    current_lap: int,
    season: int,
    round_number: int,
) -> dict[str, Any]:
    """Current field position and immediate track-position neighbors for one driver.

    Bounded to lap_number <= current_lap — previously unbounded, which read
    each driver's absolute-latest DB row regardless of the requesting
    driver's own current lap. For a session replayed/backfilled from
    ingest_historical.py (the whole race already in lap_data ahead of any one
    driver's own in-progress prediction), that silently ordered the field by
    FINISHING position and used race-END cumulative-time gaps for every
    prediction — undercut_score/overcut_score came out frozen and mostly
    saturated for a whole replay (see CLAUDE.md's Deferred Wiring entry this
    closes). Same fix pattern already proven in this module's own
    _build_race_state (position_subq/ref_lap, the Monte Carlo /simulate path)
    — this is that same pattern's second call site catching up.

    Falls back to the live-authoritative f1:{season}:{round}:gaps Redis key
    (_resolve_position_context_from_redis) whenever the bounded lap_data
    query can't resolve driver_id's own position — the case for a live
    session during its brief connection window before enough GapToLeader
    messages have streamed to rank anyone (ingest_live_session.py's
    _recompute_positions), or for any lap ingested before Checkpoint 1's live
    ingestor fix landed (position was never set at all previously; ON
    CONFLICT DO NOTHING means those rows stay NULL permanently — same
    accepted-limitation shape as the compound-tracking gap documented in
    docs/day36-fixes.md's Bug 5). A historical session never needs this
    fallback: ingest_historical.py populates position on every row.

    Uses the same "latest LapData row per driver, ordered by position" pattern
    as _build_race_state below and alert_service._latest_positions — the
    established convention for cross-driver field state in this codebase.
    gap_to_car_ahead/behind mirror pit_predictor.add_gap_features' training-time
    definition (cumulative race time difference by position, capped at
    pit_predictor.MAX_GAP_SECONDS).

    Args:
        db: Async DB session.
        async_redis_client: Async Redis client, for the live-gaps fallback.
        session_id: Session to read.
        driver_id: Driver to locate within the field.
        current_lap: Only consider lap_data rows at or before this lap.
        season, round_number: Race weekend identifiers, for the Redis
            fallback's key.
    Returns:
        Dict with position, gap_to_car_ahead, gap_to_car_behind,
        target_ahead_driver_id, target_behind_driver_id. The two target ids are
        None for the leader/last car, and all fields fall back to
        MAX_GAP_SECONDS/no-target/back-of-field when driver_id has no
        resolvable position at all (neither a bounded lap_data row nor a
        live-gaps entry) — e.g. the very first lap ingested, before anyone
        has a position yet.
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
        redis_result = await _resolve_position_context_from_redis(
            async_redis_client, season, round_number, driver_id
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


async def _resolve_inference_context(
    db: AsyncSession,
    async_redis_client: aioredis.Redis,  # type: ignore[type-arg]
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
    compound: str,
    current_lap: int,
) -> dict[str, Any]:
    """Resolve circuit/season/round/total_laps/weather/position context for one driver+lap.

    Args:
        db: Async DB session.
        async_redis_client: Async Redis client, for the live weather key.
        session_id: Session the lap belongs to.
        driver_id: Driver to resolve field position/neighbors for.
        compound: Current tyre compound, for the weather DB-average fallback.
        current_lap: This prediction's own lap number — bounds
            _resolve_position_context's field-position query (see that
            function's docstring).
    Returns:
        Dict with circuit_id, circuit_name, season, round_number, total_laps,
        track_temp, air_temp, plus _resolve_position_context's position,
        gap_to_car_ahead, gap_to_car_behind, target_ahead_driver_id,
        target_behind_driver_id.
    """
    context_query = (
        select(Race.circuit_id, Race.season, Race.round_number, Circuit.name)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .join(Circuit, Race.circuit_id == Circuit.id)
        .where(SessionModel.id == session_id)
    )
    circuit_id, season, round_number, circuit_name = (await db.execute(context_query)).one()

    total_laps_query = select(func.max(LapData.lap_number)).where(LapData.session_id == session_id)
    total_laps = (await db.execute(total_laps_query)).scalar_one()

    track_temp, air_temp = await _resolve_weather(
        async_redis_client, db, season, round_number, circuit_id, compound
    )
    position_context = await _resolve_position_context(
        db, async_redis_client, session_id, driver_id, current_lap, season, round_number
    )

    return {
        "circuit_id": circuit_id,
        "circuit_name": circuit_name,
        "season": int(season),
        "round_number": int(round_number),
        "total_laps": int(total_laps) if total_laps is not None else None,
        "track_temp": track_temp,
        "air_temp": air_temp,
        **position_context,
    }


def _run_inference(
    models: dict[str, Any],
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None],
    context: dict[str, Any],
    resolved: dict[str, Any],
    driver_id: uuid.UUID,
) -> dict[str, Any]:
    """Run the strategy models for one driver/lap context.

    pit_predictor uses its full 8-column FEATURE_COLUMNS vector, using the
    position/gap context _resolve_inference_context resolves plus
    predicted_life_remaining/safety_car_probability computed below from the
    already-loaded tire_deg/safety_car models — the same approach
    train_models.py uses to build these two features at training time.
    tire_deg uses its 6-column FEATURE_COLUMNS vector as of 2026-07-16 (see
    tire_deg_model.py's module docstring: track_temp/air_temp were reverted
    out after regressing holdout MAE, pending a weather-aware retrain+
    promotion) — resolved["track_temp"]/["air_temp"] are still resolved by
    _resolve_inference_context but intentionally unused here. undercut_score/
    overcut_score are NOT set here — they need awaited calls into
    strategy_service and are filled in by the caller, _persist_and_publish,
    after this function returns.

    Args:
        models: Loaded model registry, keyed by filename.
        maps_cache: Output of _load_encoding_maps() — this driver/lap's real
            training-time driver_id_encoded/circuit_id_encoded, resolved
            against the same compound's model this call is about to feed.
        context: Driver + lap context — expects compound, tyre_age_laps, lap_number.
        resolved: Output of _resolve_inference_context — circuit_id, circuit_name,
            total_laps, track_temp, air_temp, position, gap_to_car_ahead,
            gap_to_car_behind.
        driver_id: Driver this prediction is for, for the driver_id_encoded feature.
    Returns:
        Prediction fields matching the StrategyPrediction model; undercut_score,
        overcut_score, and confidence_score are placeholder 0.0, overwritten
        by the caller (_persist_and_publish, the latter via
        _compute_recommendation_fields). recommended_pit_lap/window_start/
        window_end/recommended_compound/explanation are NOT included here at
        all — they're added entirely by _compute_recommendation_fields.
    """
    compound = str(context.get("compound", "")).upper()
    suffix = _COMPOUND_TO_MODEL_SUFFIX.get(compound, "medium")
    deg_model = models.get(f"tire_deg_{suffix}.pkl")
    pit_model = models.get("pit_predictor.pkl")
    sc_model = models.get("safety_car_model.pkl")

    lap_number = int(context.get("lap_number", 0))
    tyre_age_laps = int(context.get("tyre_age_laps", 0))
    total_laps = resolved["total_laps"] or lap_number
    compound_encoded = _COMPOUND_ENCODING.get(compound, _COMPOUND_ENCODING["MEDIUM"])
    compound_maps = _encoding_maps_for_compound(maps_cache, compound)
    circuit_code = tire_deg_model.resolve_circuit_code(compound_maps, resolved["circuit_name"])
    driver_code = tire_deg_model.resolve_driver_code(compound_maps, str(driver_id))

    fuel_at_lap = tire_deg_model.ASSUMED_START_FUEL_KG * (1 - lap_number / max(total_laps, 1))
    fuel_load_penalty = float(
        tire_deg_model.fuel_load_penalty_seconds(lap_number, float(max(total_laps, 1)))
    )

    # Fallback default — used both when a model never loaded (deg_model is
    # None) and when a loaded model raises during inference (corrupt
    # weights, a shape mismatch, etc.): either way the worker must not crash,
    # it must degrade to a null prediction and keep processing subsequent
    # laps/drivers. predicted_life_remaining (laps until predicted
    # degradation crosses tire_deg_model.DEGRADATION_THRESHOLD_SECONDS) is
    # the ONLY tire_deg output this function needs — the raw per-lap
    # lap_time_delta prediction deg_model.predict() itself returns is used
    # only as an input to predict_life_remaining_batch below, never
    # persisted directly (that WAS a bug: StrategyPrediction.
    # tire_life_remaining previously stored this raw, sometimes-negative
    # delta instead of a genuine laps-remaining count — see CLAUDE.md's
    # now-closed Deferred Wiring entry, fixed in this function's return
    # dict below).
    predicted_life_remaining = float(tire_deg_model.MAX_LOOKAHEAD_LAPS)
    if deg_model is not None:
        try:
            with f1_ml_inference_duration_seconds.labels(model="tire_deg").time():
                predicted_life_remaining = float(
                    tire_deg_model.predict_life_remaining_batch(
                        deg_model,
                        np.array([lap_number]),
                        np.array([compound_encoded]),
                        np.array([tyre_age_laps]),
                        np.array([fuel_load_penalty]),
                        np.array([circuit_code]),
                        np.array([driver_code]),
                    )[0]
                )
        except Exception as exc:  # noqa: BLE001 — degrade to null prediction, never crash the worker
            sentry_sdk.capture_exception(exc)
            logger.warning(
                "tire_deg inference failed for driver %s, falling back to null prediction",
                driver_id,
                exc_info=True,
            )
            predicted_life_remaining = float(tire_deg_model.MAX_LOOKAHEAD_LAPS)

    safety_car_probability = 0.0
    if sc_model is not None:
        try:
            safety_car_probability = sc_model.probability_within(
                resolved["circuit_name"], lap_number, compound in _WET_COMPOUNDS, 1
            )
        except Exception as exc:  # noqa: BLE001 — degrade to null prediction, never crash the worker
            sentry_sdk.capture_exception(exc)
            logger.warning(
                "safety_car inference failed for driver %s, falling back to null prediction",
                driver_id,
                exc_info=True,
            )
            safety_car_probability = 0.0

    fuel_load_est = max(fuel_at_lap, 0.0)
    pit_features = [
        [
            tyre_age_laps,
            predicted_life_remaining,
            resolved["gap_to_car_ahead"],
            resolved["gap_to_car_behind"],
            safety_car_probability,
            total_laps - lap_number,
            resolved["position"],
            fuel_load_est,
        ]
    ]

    pit_probability = 0.0
    if pit_model is not None:
        try:
            with f1_ml_inference_duration_seconds.labels(model="pit_predictor").time():
                pit_probability = float(pit_model.predict_proba(pit_features)[0][1])
        except Exception as exc:  # noqa: BLE001 — degrade to null prediction, never crash the worker
            sentry_sdk.capture_exception(exc)
            logger.warning(
                "pit_predictor inference failed for driver %s, falling back to null prediction",
                driver_id,
                exc_info=True,
            )
            pit_probability = 0.0

    return {
        # FIXED (Checkpoint 4): was lap_number + max(int(tire_life_remaining), 1)
        # using the OLD tire_life_remaining (the raw lap_time_delta prediction,
        # a small ±2s float with no laps-count meaning) — collapsed to
        # current_lap + 1 almost always. predicted_life_remaining is the
        # genuine laps-until-degradation-threshold count.
        "optimal_pit_lap": lap_number + max(int(predicted_life_remaining), 1),
        "pit_probability": pit_probability,
        "undercut_score": 0.0,
        "overcut_score": 0.0,
        # FIXED (Checkpoint 4): was the raw tire_deg lap_time_delta prediction
        # (sometimes negative, no "remaining laps" meaning despite the column
        # name) — see CLAUDE.md's now-closed Deferred Wiring entry.
        "tire_life_remaining": predicted_life_remaining,
        # Placeholder, overwritten by _persist_and_publish's own call to
        # _compute_recommendation_fields (Checkpoint 4) — that function's
        # confidence_score is build_pit_recommendation's real Monte Carlo
        # output, not available inside this synchronous function.
        "confidence_score": 0.0,
        "model_version": _MODEL_VERSION_TAG,
    }


def _publish_prediction(session_id: uuid.UUID, prediction: dict[str, Any]) -> None:
    client = redis.Redis.from_url(get_redis_settings().redis_url, decode_responses=True)
    try:
        client.publish(f"f1:predictions:{session_id}", json.dumps(prediction, default=str))
    finally:
        client.close()


async def _resolve_undercut_overcut(
    async_redis_client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    resolved: dict[str, Any],
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
) -> tuple[float, float]:
    """Undercut/overcut scores for one driver against their immediate track-position neighbors.

    undercut_score is driver_id's probability of gaining position by pitting now
    against the car immediately ahead (strategy_service.get_undercut_score);
    overcut_score is driver_id's probability of retaining position by staying
    out while the car immediately behind pits now (get_overcut_score) — this is
    the pairing alert_service.evaluate_threats' docstring already assumes for
    undercut_score. Both are 0.0 when there's no such neighbor (leader/last
    car, per _resolve_position_context) or when a required tire degradation
    model isn't loaded for one of the two drivers.

    Args:
        async_redis_client: Async Redis client — both the cache-aside client
            strategy_service's @cacheable functions expect and the client
            _resolve_position_context's caller already opened.
        db: Async DB session.
        resolved: Output of _resolve_inference_context — season, round_number,
            target_ahead_driver_id, target_behind_driver_id.
        session_id: Session being evaluated.
        driver_id: Driver this prediction is for.
    Returns:
        (undercut_score, overcut_score).
    """
    season, round_number = resolved["season"], resolved["round_number"]
    undercut_score = 0.0
    target_ahead_driver_id = resolved["target_ahead_driver_id"]
    if target_ahead_driver_id is not None:
        try:
            result = await strategy_service.get_undercut_score(
                async_redis_client,
                db,
                season,
                round_number,
                session_id,
                driver_id,
                target_ahead_driver_id,
            )
            undercut_score = float(result["probability_pit_now_gains_position"])
        except ModelNotLoadedError:
            logger.warning(
                "undercut_score: tire degradation model not loaded for driver %s vs %s",
                driver_id,
                target_ahead_driver_id,
            )

    overcut_score = 0.0
    target_behind_driver_id = resolved["target_behind_driver_id"]
    if target_behind_driver_id is not None:
        try:
            result = await strategy_service.get_overcut_score(
                async_redis_client,
                db,
                season,
                round_number,
                session_id,
                driver_id,
                target_behind_driver_id,
            )
            overcut_score = float(result["probability_stay_out_retains_position"])
        except ModelNotLoadedError:
            logger.warning(
                "overcut_score: tire degradation model not loaded for driver %s vs %s",
                driver_id,
                target_behind_driver_id,
            )

    return undercut_score, overcut_score


def _compute_recommendation_fields(
    models: dict[str, Any],
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None],
    mae_cache: dict[str, float | None],
    driver_id: uuid.UUID,
    context: dict[str, Any],
    resolved: dict[str, Any],
    undercut_score: float,
    overcut_score: float,
) -> dict[str, Any]:
    """The Checkpoint 4 recommendation-engine fields, for the SAME StrategyPrediction
    row _run_inference's fields go into.

    Reimplements get_pit_window_with_explanation's own chain — strategy_service.
    compute_pit_recommendation -> tire_deg_recommendation_contributions ->
    pit_predictor_current_contributions -> build_pit_recommendation_explanation
    — around THIS task's own already-resolved per-lap state (context/resolved)
    instead of that function's own DB reads (strategy_service._current_state /
    _resolve_field_neighbors). This is not optional or a performance shortcut:
    process_lap (a separate, independently-ordered Celery task dispatched
    alongside run_strategy_prediction by the same ingestor — see
    ingest_live_session.py/replay_pipeline.py) may not have committed THIS
    lap's own LapData row by the time this task runs, so a fresh DB query for
    "the driver's latest lap" is not guaranteed to see it — exactly why
    _run_inference itself already takes context directly rather than
    re-querying (see that function's own docstring). compute_pit_recommendation
    was split out of build_pit_recommendation in Checkpoint 4 specifically to
    make this state-injectable call possible.

    undercut_score/overcut_score are passed in rather than recomputed — the
    caller (_persist_and_publish) already resolved them via
    _resolve_undercut_overcut, against the SAME two neighbours
    (resolved["target_ahead_driver_id"]/target_behind_driver_id) this
    function's own explanation needs; calling strategy_service.
    get_undercut_score/get_overcut_score a second time here would be pure
    redundant work (each call is itself a cache-aside Monte Carlo).

    Args:
        models, maps_cache, mae_cache: This process's loaded model registry
            and its two per-tire_deg-model sidecar caches.
        driver_id: Driver this prediction is for.
        context: Driver + lap context — expects compound, tyre_age_laps, lap_number.
        resolved: Output of _resolve_inference_context — circuit_name, total_laps,
            position, gap_to_car_ahead, gap_to_car_behind, target_ahead_driver_id,
            target_behind_driver_id.
        undercut_score, overcut_score: Already resolved by the caller.
    Returns:
        Dict with recommended_pit_lap, window_start, window_end,
        recommended_compound, confidence_score, explanation (a plain JSON-
        serializable dict via .model_dump(mode="json"), or None) — all
        None/0.0/None if build_pit_recommendation's own candidate search
        returns nothing (e.g. current_lap >= total_laps, race essentially
        over) or any sub-computation raises. Degrades gracefully, never
        crashes the worker — same convention as every other _run_inference
        sub-computation.
    """
    empty_fields: dict[str, Any] = {
        "recommended_pit_lap": None,
        "window_start": None,
        "window_end": None,
        "recommended_compound": None,
        "confidence_score": 0.0,
        "explanation": None,
    }
    try:
        compound = str(context.get("compound", "")).upper()
        lap_number = int(context.get("lap_number", 0))
        tyre_age_laps = int(context.get("tyre_age_laps", 0))
        total_laps = resolved["total_laps"] or lap_number
        recommendation_state = {
            "lap_number": lap_number,
            "compound": compound,
            "tyre_age_laps": tyre_age_laps,
            "total_laps": total_laps,
            "circuit_name": resolved["circuit_name"],
        }

        candidates = strategy_service.compute_pit_recommendation(
            models, maps_cache, mae_cache, driver_id, recommendation_state
        )
        if not candidates:
            return empty_fields

        top = candidates[0]
        fields: dict[str, Any] = {
            "recommended_pit_lap": top["pit_lap"],
            "window_start": top["window_start"],
            "window_end": top["window_end"],
            "recommended_compound": top["recommended_compound"],
            "confidence_score": top["confidence_score"] or 0.0,
        }

        tire_deg_contributions = strategy_service.tire_deg_recommendation_contributions(
            models,
            maps_cache,
            driver_id,
            resolved["circuit_name"],
            total_laps,
            top["pit_lap"],
            top["recommended_compound"],
        )
        neighbors = {
            "position": resolved["position"],
            "gap_to_car_ahead": resolved["gap_to_car_ahead"],
            "gap_to_car_behind": resolved["gap_to_car_behind"],
            "target_ahead_driver_id": resolved["target_ahead_driver_id"],
            "target_behind_driver_id": resolved["target_behind_driver_id"],
        }
        pit_predictor_contributions = strategy_service.pit_predictor_current_contributions(
            models, maps_cache, driver_id, recommendation_state, neighbors
        )
        explanation = strategy_service.build_pit_recommendation_explanation(
            pit_lap=top["pit_lap"],
            recommended_compound=top["recommended_compound"],
            confidence=top["confidence_score"],
            tyre_age_laps=tyre_age_laps,
            position=neighbors["position"],
            gap_to_car_ahead=neighbors["gap_to_car_ahead"],
            target_ahead_driver_id=neighbors["target_ahead_driver_id"],
            gap_to_car_behind=neighbors["gap_to_car_behind"],
            target_behind_driver_id=neighbors["target_behind_driver_id"],
            undercut_score=(
                undercut_score if neighbors["target_ahead_driver_id"] is not None else None
            ),
            overcut_score=(
                overcut_score if neighbors["target_behind_driver_id"] is not None else None
            ),
            tire_deg_contributions=tire_deg_contributions,
            pit_predictor_contributions=pit_predictor_contributions,
        )
        fields["explanation"] = explanation.model_dump(mode="json")
        return fields
    except Exception as exc:  # noqa: BLE001 — degrade to null recommendation, never crash the worker
        sentry_sdk.capture_exception(exc)
        logger.warning(
            "pit recommendation computation failed for driver %s, falling back to null",
            driver_id,
            exc_info=True,
        )
        return empty_fields


async def _persist_and_publish(context: dict[str, Any]) -> None:
    models = _load_models()
    maps_cache = _load_encoding_maps()
    mae_cache = _load_holdout_mae()

    session_id = uuid.UUID(str(context["session_id"]))
    driver_id = uuid.UUID(str(context["driver_id"]))
    compound = str(context.get("compound", "")).upper()
    lap_number = int(context.get("lap_number", 0))

    async_redis_client: aioredis.Redis = aioredis.from_url(  # type: ignore[type-arg]
        get_redis_settings().redis_url, decode_responses=True
    )
    session_factory = _get_session_factory()
    try:
        async with session_factory() as db:
            resolved = await _resolve_inference_context(
                db, async_redis_client, session_id, driver_id, compound, lap_number
            )
            prediction = _run_inference(models, maps_cache, context, resolved, driver_id)
            undercut_score, overcut_score = await _resolve_undercut_overcut(
                async_redis_client, db, resolved, session_id, driver_id
            )
            prediction["undercut_score"] = undercut_score
            prediction["overcut_score"] = overcut_score
            prediction.update(
                _compute_recommendation_fields(
                    models,
                    maps_cache,
                    mae_cache,
                    driver_id,
                    context,
                    resolved,
                    undercut_score,
                    overcut_score,
                )
            )

            row = StrategyPrediction(
                id=uuid.uuid4(),
                session_id=session_id,
                driver_id=driver_id,
                predicted_at=datetime.now(UTC),
                lap_number=int(context.get("lap_number", 0)),
                **prediction,
            )
            db.add(row)
            await db.commit()
            f1_strategy_predictions_total.inc()

            # Real DB alerts (evaluate_threats writes Alert rows + publishes to
            # f1:alerts:{session_id}) — deliberately separate from
            # alert_worker.py's FCM-only pubsub path, see alert_service.py's
            # module docstring. A failure here must not roll back or fail the
            # StrategyPrediction persist above, which already succeeded.
            try:
                await alert_service.evaluate_threats(db, async_redis_client, session_id)
            except Exception as exc:  # noqa: BLE001 — degrade gracefully, never crash the worker
                sentry_sdk.capture_exception(exc)
                logger.warning(
                    "evaluate_threats failed for session %s after driver %s's prediction",
                    session_id,
                    driver_id,
                    exc_info=True,
                )
    finally:
        await async_redis_client.aclose()  # type: ignore[attr-defined]

    # See telemetry_worker._persist_lap for why this dispose is required.
    await get_engine().dispose()

    _publish_prediction(
        session_id, {**prediction, "session_id": str(session_id), "driver_id": str(driver_id)}
    )


@app.task(name="run_strategy_prediction")  # type: ignore[untyped-decorator]
def run_strategy_prediction(context: dict[str, Any]) -> None:
    """Run the strategy ML models for one driver/lap context, persist and publish the result.

    Args:
        context: Driver + lap context dict (session_id, driver_id, lap_number,
            compound, tyre_age_laps).
    Returns:
        None.
    """
    asyncio.run(_persist_and_publish(context))


# --- run_race_simulation: wires race_simulator.py for the first time (Day 11) ---


async def _cumulative_race_time(
    db: AsyncSession, session_id: uuid.UUID, driver_id: uuid.UUID, up_to_lap: int
) -> float:
    """Elapsed race time for one driver through up_to_lap.

    Duplicated from strategy_service._cumulative_race_time — same no-cross-
    service-import reason as _resolve_weather above.

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


async def _build_race_state(
    db: AsyncSession,
    async_redis_client: aioredis.Redis,  # type: ignore[type-arg]
    session_id: uuid.UUID,
    requesting_driver_id: uuid.UUID,
    current_lap: int,
    current_compound: str,
    current_tyre_age: int,
    total_laps: int,
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None],
) -> RaceSimulationInput:
    """Build a full-field RaceSimulationInput: every driver's latest state, requester overridden.

    Every OTHER driver's compound/tyre age/position comes from their latest
    persisted lap (a meaningful field-wide Monte Carlo needs everyone's real
    current state, not just the requester's). The requesting driver's own
    compound/tyre_age/lap is overridden with the request's own values instead
    of their DB row — the request is the client's authoritative "starting
    point" for the what-if, which may be ahead of what's persisted.

    Args:
        db: Async DB session.
        async_redis_client: Async Redis client, for weather resolution.
        session_id: Session to build the field state from.
        requesting_driver_id: The driver running the what-if.
        current_lap, current_compound, current_tyre_age: The request's own
            state for requesting_driver_id (overrides their DB row).
        total_laps: current_lap + remaining_laps (from the request).
        maps_cache: Output of _load_encoding_maps() — each driver's real
            driver_id_encoded is resolved against THEIR OWN current compound's
            map (see the driver loop below). circuit_id_encoded is a single
            value on RaceSimulationInput shared across every compound group
            inside race_simulator._tire_deg_predictions, so it is resolved
            against current_compound's map specifically (the requesting
            driver's own compound) — a deliberate, documented simplification,
            not an oversight: correctness for every OTHER compound's group
            depends on that compound's map agreeing with current_compound's,
            true whenever all promoted tire_deg models share one training run
            (train_models.py fits driver/circuit codes once, across all 5
            compounds together — the common case) and only approximate
            otherwise. A fully general fix needs RaceSimulationInput's
            circuit_id_encoded to become per-compound, which is a
            race_simulator.py data-model change out of scope here.
    Returns:
        RaceSimulationInput ready for race_simulator.simulate_race.
    Raises:
        NoResultFound: No session with this ID exists (via the context query's .one()).
    """
    context_query = (
        select(Race.circuit_id, Race.season, Race.round_number, Circuit.name)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .join(Circuit, Race.circuit_id == Circuit.id)
        .where(SessionModel.id == session_id)
    )
    circuit_id, season, round_number, circuit_name = (await db.execute(context_query)).one()

    track_temp, air_temp = await _resolve_weather(
        async_redis_client, db, season, round_number, circuit_id, current_compound
    )

    subq = (
        select(LapData.driver_id, func.max(LapData.lap_number).label("max_lap"))
        .where(LapData.session_id == session_id)
        .group_by(LapData.driver_id)
        .subquery()
    )
    join_condition = (LapData.driver_id == subq.c.driver_id) & (
        LapData.lap_number == subq.c.max_lap
    )
    latest_laps_query = (
        select(LapData).join(subq, join_condition).where(LapData.session_id == session_id)
    )
    latest_laps = list((await db.execute(latest_laps_query)).scalars().all())

    # Field position as of current_lap specifically — NOT each driver's own
    # absolute-latest DB row (that's what latest_laps above is for, and it's
    # fine for compound/tyre_age, but for a completed/ahead-of-current_lap
    # session it would silently be each driver's FINAL classification
    # position rather than their position at the point the what-if starts).
    # Same "anchor to current_lap" fix as cumulative_race_time_seconds below.
    position_subq = (
        select(LapData.driver_id, func.max(LapData.lap_number).label("ref_lap"))
        .where(LapData.session_id == session_id, LapData.lap_number <= current_lap)
        .group_by(LapData.driver_id)
        .subquery()
    )
    position_join = (LapData.driver_id == position_subq.c.driver_id) & (
        LapData.lap_number == position_subq.c.ref_lap
    )
    # Also selects session_elapsed_seconds off the SAME reference row this
    # join already resolves ("latest row <= current_lap per driver") — a
    # real absolute elapsed time for a backfilled historical session,
    # comparable across drivers regardless of differing NULL-lap-time
    # counts (see CLAUDE.md Deferred Wiring item A and backfill_lap_
    # session_time.py). Reusing this join instead of a separate query
    # avoids adding a third DB round trip alongside cumulative_time_query
    # below, which stays as the fallback source for a live-ingested
    # (never-backfilled) session.
    position_query = (
        select(LapData.driver_id, LapData.position, LapData.session_elapsed_seconds)
        .join(position_subq, position_join)
        .where(LapData.session_id == session_id)
    )
    position_rows = (await db.execute(position_query)).all()
    position_by_driver: dict[uuid.UUID, int | None] = {row[0]: row[1] for row in position_rows}
    elapsed_by_driver: dict[uuid.UUID, float | None] = {row[0]: row[2] for row in position_rows}

    # Fallback source for cumulative_race_time_seconds when elapsed_by_driver
    # has no value for a driver (a live-ingested session, never backfilled —
    # see backfill_lap_session_time.py). Batched replacement for what was
    # previously one _cumulative_race_time() call per driver inside the loop
    # below (an N+1: ~20 separate DB round trips for a 20-driver field, the
    # confirmed dominant cost behind this task's 65-88s end-to-end runtime —
    # see CLAUDE.md's Deferred Wiring "Single --pool=solo Celery worker"
    # entry). Same filter shape as _cumulative_race_time's own SUM fallback
    # (session_id, lap_number <= current_lap, lap_time_seconds IS NOT NULL),
    # just grouped by driver_id instead of scoped to one — every driver in
    # this loop shares the same up_to_lap (current_lap), so one GROUP BY
    # query covers all of them.
    # Also selects each driver's own real median lap_time_seconds through
    # current_lap (percentile_cont(0.5), Postgres's SQL median) in the same
    # query — see DriverRaceState.baseline_lap_time_seconds. This is the
    # exact same definition tire_deg_model.add_engineered_features uses for
    # lap_time_delta's own baseline (df.groupby(["session_id",
    # "driver_id"])["lap_time_seconds"].median()), just bounded to
    # lap_number <= current_lap since a forward simulation can't see the
    # session's full median. Reuses this query's existing GROUP BY
    # (driver_id) / WHERE (lap_time_seconds IS NOT NULL, matching pandas
    # .median()'s implicit NaN-skip) shape rather than adding a fourth DB
    # round trip. Independent of session_elapsed_seconds (item 7's fix,
    # elapsed_by_driver above) entirely: lap_time_seconds is populated the
    # same way whether or not a session has been backfilled, so there is no
    # elapsed-vs-sum-style fallback distinction for this column.
    cumulative_time_query = (
        select(
            LapData.driver_id,
            func.sum(LapData.lap_time_seconds),
            func.percentile_cont(0.5).within_group(LapData.lap_time_seconds),
        )
        .where(
            LapData.session_id == session_id,
            LapData.lap_number <= current_lap,
            LapData.lap_time_seconds.is_not(None),
        )
        .group_by(LapData.driver_id)
    )
    cumulative_time_rows = (await db.execute(cumulative_time_query)).all()
    cumulative_time_by_driver: dict[uuid.UUID, float] = {
        row[0]: float(row[1] or 0.0) for row in cumulative_time_rows
    }
    baseline_lap_time_by_driver: dict[uuid.UUID, float] = {
        row[0]: float(row[2]) for row in cumulative_time_rows if row[2] is not None
    }
    # A driver with no median of their own (e.g. zero valid timed laps
    # through current_lap) falls back to the field's own median baseline,
    # not 0.0 — 0.0 would give them an artificial ~0s/lap pace and rank them
    # P1 in the simulation regardless of their real position. Only when NO
    # driver in the field has a baseline (e.g. the pre-race zero-lap-data
    # case below) does this collapse to 0.0 for everyone, which is
    # ranking-neutral — identical to the pre-baseline behaviour — rather
    # than favouring any one driver.
    field_baseline_values = list(baseline_lap_time_by_driver.values())
    field_median_baseline = (
        float(np.median(field_baseline_values)) if field_baseline_values else 0.0
    )

    drivers: list[DriverRaceState] = []
    requesting_driver_found = False
    for lap in latest_laps:
        driver_id_str = str(lap.driver_id)
        if lap.driver_id == requesting_driver_id:
            requesting_driver_found = True
            compound, tyre_age_laps = current_compound, current_tyre_age
        else:
            compound, tyre_age_laps = lap.compound, lap.tyre_age_laps
        # Seeded against the same reference lap (current_lap) for every driver,
        # not each driver's own independently-latest ingested lap — otherwise
        # normal async ingestion skew (or the requester's current_lap running
        # ahead of their own persisted data, per this function's docstring)
        # bakes a fake multi-lap time gap into cumulative_race_time_seconds
        # that swamps real on-track gaps of a few seconds, since simulate_race
        # advances every driver in lockstep from current_lap + 1 onward.
        # Prefers elapsed_by_driver (real absolute elapsed time, from the
        # same current_lap-anchored row as starting_position below) over the
        # cumulative_time_by_driver SUM fallback — same session_elapsed_
        # seconds-first pattern as _cumulative_race_time above.
        driver_elapsed = elapsed_by_driver.get(lap.driver_id)
        cumulative_time = (
            driver_elapsed
            if driver_elapsed is not None
            else cumulative_time_by_driver.get(lap.driver_id, 0.0)
        )
        starting_position = (
            position_by_driver.get(lap.driver_id) or lap.position or len(latest_laps)
        )
        baseline_lap_time = baseline_lap_time_by_driver.get(lap.driver_id, field_median_baseline)
        # Resolved against THIS driver's own current compound — different
        # drivers in the same field can be on different compounds, whose
        # tire_deg models may have been promoted from different training
        # runs (item 9's per-compound promotion) with different driver code
        # universes; race_simulator groups drivers by compound before ever
        # calling a pipeline, so this must match that grouping exactly.
        driver_maps = _encoding_maps_for_compound(maps_cache, compound)
        drivers.append(
            DriverRaceState(
                driver_id=driver_id_str,
                starting_position=starting_position,
                compound=compound,
                compound_encoded=_COMPOUND_ENCODING.get(compound, _COMPOUND_ENCODING["MEDIUM"]),
                tyre_age_laps=tyre_age_laps,
                driver_id_encoded=tire_deg_model.resolve_driver_code(driver_maps, driver_id_str),
                cumulative_race_time_seconds=cumulative_time,
                baseline_lap_time_seconds=baseline_lap_time,
            )
        )

    # current_compound's own map — used below for the requester's fallback
    # driver code (if they had no persisted lap) and, per this function's own
    # docstring, as the resolution context for the single shared
    # circuit_id_encoded on RaceSimulationInput.
    current_maps = _encoding_maps_for_compound(maps_cache, current_compound)

    if not requesting_driver_found:
        # No persisted lap data yet for the requester (e.g. pre-race what-if) —
        # their request fields are the only state available; race starts fresh.
        # baseline_lap_time_seconds falls back to the field's own median (0.0
        # if the field has none either, e.g. a genuinely empty session) since
        # the requester has no laps of their own to compute a median from —
        # same fallback rationale as the loop above.
        driver_id_str = str(requesting_driver_id)
        drivers.append(
            DriverRaceState(
                driver_id=driver_id_str,
                starting_position=len(latest_laps) + 1,
                compound=current_compound,
                compound_encoded=_COMPOUND_ENCODING.get(
                    current_compound, _COMPOUND_ENCODING["MEDIUM"]
                ),
                tyre_age_laps=current_tyre_age,
                driver_id_encoded=tire_deg_model.resolve_driver_code(current_maps, driver_id_str),
                cumulative_race_time_seconds=0.0,
                baseline_lap_time_seconds=field_median_baseline,
            )
        )

    return RaceSimulationInput(
        circuit_name=circuit_name,
        circuit_id_encoded=tire_deg_model.resolve_circuit_code(current_maps, circuit_name),
        current_lap=current_lap,
        total_laps=total_laps,
        wet_track=current_compound in _WET_COMPOUNDS,
        track_temp=track_temp,
        air_temp=air_temp,
        drivers=drivers,
    )


class _OvertakingDriverEntry(TypedDict):
    position: int
    driver_id: str
    gap_seconds: float
    # Added for the What-If Simulator rebuild part (b) — see
    # docs/core-feature-rebuild-whatif-simulator.md §7 and
    # _build_plan_explanation's own docstring for the full rationale. Both
    # None when the simulation has no data for this rival (should not happen
    # for a driver who actually raced in the same simulate_race call, but
    # None is a genuine "unknown," never coerced to a misleading 0.0).
    finish_ahead_probability: float | None
    rival_projected_pit_lap: int | None
    rival_pit_probability: float | None


def _project_pit_stop_degradation(
    race_state: RaceSimulationInput,
    requester_state: DriverRaceState,
    pit_laps: list[int],
    compounds: list[str],
    total_laps: int,
    tire_deg_pipelines: dict[str, Any],
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None],
    laps_after_pit: int,
) -> tuple[float, float]:
    """Real tire_deg-derived (fresh_tyre_gain_per_lap, total_recoverable_seconds).

    What-If Simulator rebuild part (a) — see
    docs/core-feature-rebuild-whatif-simulator.md §7. Compares, for the
    plan's LAST forced pit stop, the OLD compound continuing to degrade (tyre
    age growing from its real value at that pit lap — "what if the driver had
    stayed out instead") against the NEW compound starting fresh at
    tyre_age=0 (what the plan actually does), both projected over
    laps_after_pit laps via tire_deg_model.project_stint_delta — the same
    shared projection strategy_service's undercut/overcut math uses. This
    replaces the previous hardcoded _FRESH_TYRE_GAIN_PER_LAP_SECONDS lookup
    with a real model-derived number.

    Only called from the pit_laps' truthy branch — the caller (this module's
    _build_plan_explanation) already guarantees pit_laps/compounds are
    non-empty.

    Args:
        race_state, requester_state: See _build_plan_explanation.
        pit_laps, compounds: This plan's forced pit schedule.
        total_laps: current_lap + remaining_laps from the request.
        tire_deg_pipelines: Fitted tire_deg pipelines keyed by compound name
            (same dict _run_one_scenario already threads into simulate_race).
        maps_cache: Output of _load_encoding_maps() — resolves driver_id_encoded/
            circuit_id_encoded per compound, same convention as _build_race_state.
        laps_after_pit: Laps remaining after the last forced pit stop
            (total_laps - pit_laps[-1], already computed by the caller).
    Returns:
        (fresh_tyre_gain_per_lap, total_recoverable_seconds). The name
        fresh_tyre_gain_per_lap is kept from before this fix (avoiding
        schema/client churn — see the What-If Simulator rebuild CP1 decision)
        despite the value no longer being a fixed positive constant: it can
        now be NEGATIVE when the new compound is genuinely a worse choice for
        the remaining laps (e.g. a dry-track INTERMEDIATE pit — see CLAUDE.md's
        "no track-condition input" tyre-model limitation) — a real signal the
        old constant could never produce.

        Falls back to the ORIGINAL hardcoded _FRESH_TYRE_GAIN_PER_LAP_SECONDS
        constant (non-regressive — an approximate number, not a lost line)
        whenever either compound's projection can't be made: a missing
        pipeline, a schema-mismatched pipeline, or predict() failing (see
        project_stint_delta's own docstring), or laps_after_pit <= 0 (nothing
        to project — also this function's own guard against a division by
        zero below).
    """
    if laps_after_pit <= 0:
        return 0.0, 0.0

    new_compound = compounds[-1]
    last_pit_lap = pit_laps[-1]

    if len(pit_laps) >= 2:
        # A later stint in a multi-stop plan: tyre age reset to 0 at the
        # PREVIOUS forced pit stop, so the old compound's age at last_pit_lap
        # is laps since that stop, and the old compound is whatever this plan
        # set at that earlier stop (compounds[-2]) — NOT requester_state.compound,
        # which is only the plan's STARTING compound (see _build_race_state's
        # docstring: requester_state is overridden from the request's own
        # current_compound, not re-derived per stint).
        stint_start_lap = pit_laps[-2]
        old_compound = compounds[-2]
    else:
        # The plan's only forced pit stop: the old compound is whatever the
        # requester started the simulation on, and their tyre age grows from
        # current_tyre_age (requester_state.tyre_age_laps) starting at
        # race_state.current_lap.
        stint_start_lap = race_state.current_lap
        old_compound = requester_state.compound

    # max(..., 0): pit_laps has no enforced chronological ordering beyond each
    # entry individually falling within (current_lap, horizon_end] (see
    # SimulateStrategyRequest._validate_pit_plan) — an out-of-order multi-stop
    # plan (unlikely from the UI's own "+ Add Pit Stop" flow, which always
    # appends, but not rejected by the schema) could otherwise make this
    # negative. Clamping degrades to "assume a fresh-ish tyre" rather than
    # feeding project_stint_delta a nonsensical negative tyre age.
    old_tyre_age_at_pit = max(last_pit_lap - stint_start_lap, 0)
    if stint_start_lap == race_state.current_lap:
        old_tyre_age_at_pit += requester_state.tyre_age_laps

    old_pipeline = tire_deg_pipelines.get(old_compound)
    new_pipeline = tire_deg_pipelines.get(new_compound)
    old_maps = _encoding_maps_for_compound(maps_cache, old_compound)
    new_maps = _encoding_maps_for_compound(maps_cache, new_compound)
    old_compound_encoded = _COMPOUND_ENCODING.get(old_compound, _COMPOUND_ENCODING["MEDIUM"])
    new_compound_encoded = _COMPOUND_ENCODING.get(new_compound, _COMPOUND_ENCODING["MEDIUM"])
    old_driver_code = tire_deg_model.resolve_driver_code(old_maps, requester_state.driver_id)
    new_driver_code = tire_deg_model.resolve_driver_code(new_maps, requester_state.driver_id)
    old_circuit_code = tire_deg_model.resolve_circuit_code(old_maps, race_state.circuit_name)
    new_circuit_code = tire_deg_model.resolve_circuit_code(new_maps, race_state.circuit_name)

    stay_out_sum = tire_deg_model.project_stint_delta(
        old_pipeline,
        old_compound_encoded,
        old_driver_code,
        old_circuit_code,
        start_lap=last_pit_lap + 1,
        n_laps=laps_after_pit,
        start_tyre_age=old_tyre_age_at_pit,
        total_laps=total_laps,
    )
    fresh_sum = tire_deg_model.project_stint_delta(
        new_pipeline,
        new_compound_encoded,
        new_driver_code,
        new_circuit_code,
        start_lap=last_pit_lap + 1,
        n_laps=laps_after_pit,
        start_tyre_age=0,
        total_laps=total_laps,
    )

    if stay_out_sum is None or fresh_sum is None:
        fallback_gain = _FRESH_TYRE_GAIN_PER_LAP_SECONDS.get(new_compound, 0.0)
        return fallback_gain, fallback_gain * laps_after_pit

    total_recoverable_seconds = stay_out_sum - fresh_sum
    fresh_tyre_gain_per_lap = total_recoverable_seconds / laps_after_pit
    return fresh_tyre_gain_per_lap, total_recoverable_seconds


def _peak_projected_pit_lap(
    distribution: race_simulator.DriverPositionDistribution | None,
) -> tuple[int | None, float | None]:
    """The single most likely pit lap for a driver, from their own projected_pit_laps.

    Summarizes race_simulator.DriverPositionDistribution.projected_pit_laps (a
    full per-lap probability list across the simulated remainder) down to one
    (lap, probability) pair for display in a drivers_overtaken row — see
    _build_plan_explanation. Both the lap AND its probability are returned
    together (never just the lap) so a caller can judge confidence rather than
    the peak lap alone implying more certainty than the distribution actually
    has. Ties resolve to the EARLIEST lap: Python's max() keeps the first-seen
    maximum, and projected_pit_laps is already sorted by lap ascending.

    Args:
        distribution: That driver's DriverPositionDistribution from this
            scenario's simulate_race call, or None if unavailable (looked up
            by driver_id from a separate dict — should always be present for
            a driver who actually raced in the same simulate_race call, but
            handled defensively since it's a separate lookup, not a direct
            reference).
    Returns:
        (lap, probability) of the highest pit-probability lap, or (None, None)
        if distribution is None or no lap has any nonzero pit probability.
    """
    if distribution is None or not distribution.projected_pit_laps:
        return None, None
    lap, probability = max(distribution.projected_pit_laps, key=lambda entry: entry[1])
    return lap, probability


def _build_plan_explanation(
    race_state: RaceSimulationInput,
    requester_state: DriverRaceState,
    pit_laps: list[int],
    compounds: list[str],
    total_laps: int,
    remaining_laps: int,
    tire_deg_pipelines: dict[str, Any],
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None],
    driver_distributions_by_id: dict[str, race_simulator.DriverPositionDistribution],
) -> dict[str, Any]:
    """Explain why a plan's position_gain_loss came out the way it did.

    drivers_overtaken lists every OTHER driver currently behind the requester
    (higher cumulative_race_time_seconds) whose gap is less than
    race_simulator.PIT_STOP_SECONDS — close enough to leapfrog the requester
    on a full pit-stop time loss. This SELECTION criterion is a static
    property of the field's gaps at current_lap, computed the same way
    regardless of whether this plan has a forced pit stop — the frontend
    relabels the same list ("overtake you" vs "you overtake") based on
    position_gain_loss's sign. Deliberately UNCHANGED by either part of the
    What-If Simulator rebuild fix — see docs/core-feature-rebuild-whatif-
    simulator.md §7's own scope decision: part (a) enriched fresh_tyre_
    gain_per_lap/total_recoverable_seconds only, and this part (b) enriches
    each row's DATA (finish_ahead_probability/rival_projected_pit_lap/
    rival_pit_probability, all real Monte Carlo outputs from THIS SAME
    simulate_race call) without touching which drivers appear in the list or
    why.

    fresh_tyre_gain_per_lap/total_recoverable_seconds are a real
    tire_deg-model-derived comparison — see _project_pit_stop_degradation's
    own docstring for the full rationale and the fallback behaviour when a
    real projection can't be made.

    Args:
        race_state: The built field state (post _build_race_state).
        requester_state: race_state.drivers entry for the requesting driver.
        pit_laps, compounds: This plan's forced pit schedule (may be empty).
        total_laps: current_lap + remaining_laps from the request.
        remaining_laps: The request's own remaining_laps — used verbatim only
            when pit_laps is empty (no forced stop to measure "after" from).
        tire_deg_pipelines, maps_cache: See _project_pit_stop_degradation.
        driver_distributions_by_id: Every driver in this SAME simulate_race
            call's result.driver_distributions, keyed by driver_id (built once
            by _run_one_scenario, not re-derived here). Used for two things:
            (1) requester_state's own DriverPositionDistribution.
            finish_ahead_probability, read per rival to answer "do I actually
            end up ahead of this specific rival?" with the real simulated
            probability, replacing what used to be an implicit assumption a
            reader could only infer from position_gain_loss overall; (2) each
            rival's OWN DriverPositionDistribution.projected_pit_laps
            (summarized via _peak_projected_pit_lap), extracted from the same
            per-lap pit_flags array simulate_race already computes for every
            driver — not a fresh assumption, unlike the frozen "rivals never
            pit" premise this explanation used to (and, per fresh_tyre_gain_
            per_lap/total_recoverable_seconds's OWN remaining static-pace
            assumption, still partly does) rely on.
    Returns:
        PlanExplanation-shaped dict.
    """
    requester_distribution = driver_distributions_by_id.get(requester_state.driver_id)
    requester_finish_ahead = (
        requester_distribution.finish_ahead_probability
        if requester_distribution is not None
        else {}
    )
    peak_pit_by_driver_id = {
        driver.driver_id: _peak_projected_pit_lap(driver_distributions_by_id.get(driver.driver_id))
        for driver in race_state.drivers
    }

    drivers_overtaken: list[_OvertakingDriverEntry] = sorted(
        (
            _OvertakingDriverEntry(
                position=driver.starting_position,
                driver_id=driver.driver_id,
                gap_seconds=driver.cumulative_race_time_seconds
                - requester_state.cumulative_race_time_seconds,
                finish_ahead_probability=requester_finish_ahead.get(driver.driver_id),
                rival_projected_pit_lap=peak_pit_by_driver_id[driver.driver_id][0],
                rival_pit_probability=peak_pit_by_driver_id[driver.driver_id][1],
            )
            for driver in race_state.drivers
            if driver.driver_id != requester_state.driver_id
            and 0.0
            < driver.cumulative_race_time_seconds - requester_state.cumulative_race_time_seconds
            < race_simulator.PIT_STOP_SECONDS
        ),
        key=lambda entry: entry["gap_seconds"],
    )

    if pit_laps:
        laps_after_pit = max(total_laps - pit_laps[-1], 0)
        fresh_tyre_gain_per_lap, total_recoverable_seconds = _project_pit_stop_degradation(
            race_state,
            requester_state,
            pit_laps,
            compounds,
            total_laps,
            tire_deg_pipelines,
            maps_cache,
            laps_after_pit,
        )
    else:
        laps_after_pit = remaining_laps
        fresh_tyre_gain_per_lap = 0.0
        total_recoverable_seconds = 0.0

    return {
        "pit_cost_seconds": race_simulator.PIT_STOP_SECONDS,
        "drivers_overtaken": drivers_overtaken,
        "remaining_laps": laps_after_pit,
        "fresh_tyre_gain_per_lap": fresh_tyre_gain_per_lap,
        "total_recoverable_seconds": total_recoverable_seconds,
    }


def _shape_position_probabilities(
    distribution: race_simulator.DriverPositionDistribution,
) -> list[dict[str, Any]]:
    """PositionProbability-shaped list from a DriverPositionDistribution, sparse and sorted.

    race_simulator.simulate_race returns a DENSE dict covering every position
    in the field (n_drivers entries) for every driver — most of which are
    0.0 for any single driver in a real ~20-car field (e.g. a midfield
    driver has genuinely zero probability of finishing P1). Filtering those
    out keeps the API response compact without losing information. Sorted
    by position ascending (not probability descending) so a frontend chart's
    x-axis renders in natural finishing-position order regardless of the
    source dict's insertion order.

    Args:
        distribution: One driver's DriverPositionDistribution from a
            race_simulator.simulate_race call.
    Returns:
        PositionProbability-shaped dicts, position ascending, probability > 0.0 only.
    """
    return [
        {"position": position, "probability": probability}
        for position, probability in sorted(distribution.position_probabilities.items())
        if probability > 0.0
    ]


def _run_one_scenario(
    race_state: RaceSimulationInput,
    requester_state: DriverRaceState,
    requesting_driver_id: uuid.UUID,
    tire_deg_pipelines: dict[str, Any],
    pit_model: Any,
    sc_model: Any,
    maps_cache: dict[str, tire_deg_model.CategoricalEncodingMaps | None],
    pit_laps: list[int],
    compounds: list[str],
    total_laps: int,
    remaining_laps: int,
    label: str | None,
    rng_seed: int | None,
) -> dict[str, Any]:
    """One race_simulator.simulate_race call for one candidate plan, shaped as SimulatedRaceOutcome.

    Shared by both the single-plan path (SimulateStrategyRequest.scenarios
    omitted) and the multi-scenario compare path (Checkpoint 3, see
    docs/core-feature-rebuild-whatif-simulator.md) — the only difference
    between the two is how many times this is called per request and
    whether rng_seed is shared across those calls.

    Args:
        race_state: The full-field state, built ONCE per request (by
            _build_race_state) regardless of how many scenarios are run
            against it — this is the whole point of the server-orchestrated
            design: identical DB-derived field state, N simulate_race calls.
        requester_state: race_state.drivers entry for the requesting driver —
            a property of the STARTING state, identical for every scenario
            in this request (not something simulate_race's outcome affects).
        requesting_driver_id: The driver running the what-if.
        tire_deg_pipelines, pit_model, sc_model: Loaded ML models.
        maps_cache: Output of _load_encoding_maps() — threaded into
            _build_plan_explanation for its real tire_deg-derived degradation
            comparison (What-If Simulator rebuild part (a), see
            docs/core-feature-rebuild-whatif-simulator.md §7).
        pit_laps, compounds: This scenario's forced pit plan — may be empty
            (that scenario's pit timing is left fully model-driven).
        total_laps, remaining_laps: Request-level race-length context,
            identical across every scenario in one request.
        label: This scenario's optional display label (ScenarioPlan.label),
            passed through verbatim — None on the single-plan path, which
            has no ScenarioPlan to carry one from.
        rng_seed: None on the single-plan path (unchanged, non-reproducible
            behaviour — see race_simulator.simulate_race's own docstring).
            A shared seed across every call in a multi-scenario request so
            scenarios use the SAME safety-car/lap-noise draws (a Monte Carlo
            "common random numbers" technique) — isolating the comparison to
            each scenario's own pit-lap decision rather than also comparing
            independently-drawn randomness. This works because every
            scenario in one request shares current_lap/total_laps (so
            simulate_race's per-lap loop makes the identical number/order of
            RNG draws regardless of forced_pit_laps' content — confirmed by
            test_forced_pit_laps_changes_outcome_only_for_that_driver in
            test_race_simulator.py, which already relies on this same
            property for an untouched driver within a single simulate_race
            call).
    Returns:
        SimulatedRaceOutcome-shaped dict.
    """
    forced_pit_laps: dict[str, dict[int, tuple[str, int]]] | None = None
    if pit_laps:
        schedule = {
            lap: (compound, _COMPOUND_ENCODING.get(compound, _COMPOUND_ENCODING["MEDIUM"]))
            for lap, compound in zip(pit_laps, compounds, strict=True)
        }
        forced_pit_laps = {str(requesting_driver_id): schedule}

    with f1_ml_inference_duration_seconds.labels(model="race_simulator").time():
        result = race_simulator.simulate_race(
            race_state,
            tire_deg_pipelines,
            pit_model,
            sc_model,
            forced_pit_laps=forced_pit_laps,
            rng_seed=rng_seed,
        )

    requester_id_str = str(requesting_driver_id)
    # Built once per scenario, keyed by driver_id — feeds _build_plan_
    # explanation's finish_ahead_probability/projected-pit-lap enrichment
    # (What-If Simulator rebuild part (b), see docs/core-feature-rebuild-
    # whatif-simulator.md §7): every driver's own DriverPositionDistribution
    # from THIS scenario's simulate_race call, not a fresh computation.
    driver_distributions_by_id = {d.driver_id: d for d in result.driver_distributions}
    requesting_distribution = driver_distributions_by_id[requester_id_str]
    position_gain_loss = round(
        requester_state.starting_position - requesting_distribution.mean_position
    )
    explanation = _build_plan_explanation(
        race_state,
        requester_state,
        pit_laps,
        compounds,
        total_laps,
        remaining_laps,
        tire_deg_pipelines,
        maps_cache,
        driver_distributions_by_id,
    )

    return {
        "pit_laps": pit_laps,
        "compounds": compounds,
        "label": label,
        "predicted_finish_time": requesting_distribution.mean_finish_time_seconds,
        "position_gain_loss": position_gain_loss,
        "mean_position": requesting_distribution.mean_position,
        "position_probabilities": _shape_position_probabilities(requesting_distribution),
        "confidence_interval": (
            requesting_distribution.finish_time_p5_seconds,
            requesting_distribution.finish_time_p95_seconds,
        ),
        "explanation": explanation,
    }


async def _run_simulation(payload: dict[str, Any]) -> dict[str, Any]:
    """Build race state from DB + request, run the Monte Carlo simulation(s), shape the result.

    Args:
        payload: session_id plus the SimulateStrategyRequest fields (driver_id,
            current_lap, current_compound, current_tyre_age, remaining_laps,
            pit_laps, compounds, scenarios — pit_laps/compounds and each
            scenario's own pit_laps/compounds are already length-matched and
            compound-validated by SimulateStrategyRequest's model_validator).
    Returns:
        SimulateStrategyResponse-shaped dict (JSON-serialisable). strategies
        has exactly one entry for the single-plan path (scenarios omitted,
        unchanged from before Checkpoint 3), or one entry per scenario, in
        request order, for a multi-scenario compare request.
    Raises:
        NotFoundError: No session with this ID exists.
        ValidationError: current_lap exceeds this session's real progress by
            more than one lap — see strategy_service.validate_current_lap's
            own docstring. Checked here too (defense in depth), not just in
            apis/v1/strategy.py's simulate_strategy route: this task can be
            enqueued directly (run_race_simulation.delay/.run), bypassing the
            route entirely, and must not be able to skip the check that way.
            Raising here degrades to a Celery task FAILURE (logged, no
            result stored) rather than silently running phantom laps beyond
            the session's actual race distance — see
            docs/simulator-issues-wet-model-and-position-context.md's
            Checkpoint-6 follow-up finding.
    """
    models = _load_models()
    maps_cache = _load_encoding_maps()
    tire_deg_pipelines = {
        compound: models[f"tire_deg_{suffix}.pkl"]
        for compound, suffix in _COMPOUND_TO_MODEL_SUFFIX.items()
    }
    pit_model = models["pit_predictor.pkl"]
    sc_model = models["safety_car_model.pkl"]

    session_id = uuid.UUID(str(payload["session_id"]))
    requesting_driver_id = uuid.UUID(str(payload["driver_id"]))
    current_compound = str(payload["current_compound"]).upper()
    current_lap = int(payload["current_lap"])
    current_tyre_age = int(payload["current_tyre_age"])
    total_laps = current_lap + int(payload["remaining_laps"])
    pit_laps = [int(lap) for lap in payload.get("pit_laps", [])]
    compounds = [str(c).upper() for c in payload.get("compounds", [])]
    raw_scenarios: list[dict[str, Any]] | None = payload.get("scenarios")

    async_redis_client: aioredis.Redis = aioredis.from_url(  # type: ignore[type-arg]
        get_redis_settings().redis_url, decode_responses=True
    )
    session_factory = _get_session_factory()
    try:
        async with session_factory() as db:
            await strategy_service.validate_current_lap(db, session_id, current_lap)
            race_state = await _build_race_state(
                db,
                async_redis_client,
                session_id,
                requesting_driver_id,
                current_lap,
                current_compound,
                current_tyre_age,
                total_laps,
                maps_cache,
            )
    finally:
        await async_redis_client.aclose()  # type: ignore[attr-defined]
        # See telemetry_worker._persist_lap for why this dispose is required.
        # In its own finally (not just after the try/finally above, as this
        # was before validate_current_lap existed): validate_current_lap
        # raising here is now the expected, common rejection path for a bad
        # current_lap, not a rare failure — skipping dispose on that path
        # would leak the pooled connection into a later, different-loop
        # asyncio.run() far more often than the original rare-NoResultFound
        # case this comment already accounted for.
        await get_engine().dispose()

    requester_id_str = str(requesting_driver_id)
    requester_state = next(d for d in race_state.drivers if d.driver_id == requester_id_str)
    remaining_laps = int(payload["remaining_laps"])

    if raw_scenarios:
        # Common random numbers (see _run_one_scenario's docstring): one seed,
        # shared across every scenario in THIS request, so the comparison
        # isolates each scenario's own pit-lap decision rather than also
        # comparing independently-drawn safety-car/noise randomness. A fresh
        # seed per request (not a fixed constant) — different requests must
        # still see independent Monte Carlo outcomes.
        shared_seed = secrets.randbelow(2**31)
        strategies = [
            _run_one_scenario(
                race_state,
                requester_state,
                requesting_driver_id,
                tire_deg_pipelines,
                pit_model,
                sc_model,
                maps_cache,
                pit_laps=[int(lap) for lap in scenario.get("pit_laps", [])],
                compounds=[str(c).upper() for c in scenario.get("compounds", [])],
                total_laps=total_laps,
                remaining_laps=remaining_laps,
                label=scenario.get("label"),
                rng_seed=shared_seed,
            )
            for scenario in raw_scenarios
        ]
    else:
        strategies = [
            _run_one_scenario(
                race_state,
                requester_state,
                requesting_driver_id,
                tire_deg_pipelines,
                pit_model,
                sc_model,
                maps_cache,
                pit_laps=pit_laps,
                compounds=compounds,
                total_laps=total_laps,
                remaining_laps=remaining_laps,
                label=None,
                # Unchanged from before Checkpoint 3: the single-plan path
                # stays unseeded/non-reproducible — there is only one call,
                # so there is nothing to hold common across, and changing
                # this would alter existing behaviour outside this
                # checkpoint's scope.
                rng_seed=None,
            )
        ]

    return {
        "driver_id": requester_id_str,
        "starting_position": requester_state.starting_position,
        "strategies": strategies,
    }


@app.task(name="run_race_simulation")  # type: ignore[untyped-decorator]
def run_race_simulation(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a Monte Carlo what-if race simulation, return the shaped result.

    Args:
        payload: session_id plus the SimulateStrategyRequest fields.
    Returns:
        SimulateStrategyResponse-shaped dict. Celery's Redis result backend
        stores this against the task_id automatically (see celery_app.py) —
        unlike run_strategy_prediction, nothing is persisted to Postgres or
        published to pub/sub, since this is a pure request/response
        computation, not a live-session side effect.
    """
    return asyncio.run(_run_simulation(payload))
