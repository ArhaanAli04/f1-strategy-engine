"""Celery task that runs the strategy ML models and persists + publishes predictions."""

import asyncio
import json
import logging
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
from backend.services.ml import pit_predictor, race_simulator, tire_deg_model
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
    key = f"{_MODEL_VERSION_TAG}/{filename}"
    client.download_file(settings.aws_bucket_name, key, str(path))
    return path


def _local_metrics_path(filename: str) -> Path:
    model_dir = Path(get_ml_settings().model_cache_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir / f"{filename}.metrics.json"


def _download_metrics_from_s3(filename: str) -> dict[str, Any] | None:
    """Download a tire_deg model's own sidecar metrics.json from S3, unless cached locally.

    Duplicated from strategy_service.py's identical helper — same no-cross-service-
    import convention as this module's other duplicated helpers (_resolve_weather,
    _encoding_maps_for_compound). Same local-disk-cache-then-fetch lifecycle as _download_from_s3
    (never re-fetches once cached — a worker restart is what picks up a newly-promoted
    model's fresh sidecar, same as every other model in this process).

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
    categorical encoding" section) — same once-per-process lifecycle as the models
    themselves, so this function is the single place both caches get populated
    together, which is what lets apply_incompatible_model_fallbacks alias both in
    lockstep below.

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
    # Guards against a stale/schema-incompatible production model (e.g. the
    # 8-feature tire_deg_wet.pkl leftover from the reverted weather
    # experiment — see docs/simulator-issues-wet-model-and-position-
    # context.md) by aliasing it (and its encoding maps) to a compatible
    # fallback for this process.
    tire_deg_model.apply_incompatible_model_fallbacks(_model_cache, _encoding_maps_cache)
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


async def _resolve_position_context(
    db: AsyncSession, session_id: uuid.UUID, driver_id: uuid.UUID
) -> dict[str, Any]:
    """Current field position and immediate track-position neighbors for one driver.

    Uses the same "latest LapData row per driver, ordered by position" pattern
    as _build_race_state below and alert_service._latest_positions — the
    established convention for cross-driver field state in this codebase.
    gap_to_car_ahead/behind mirror pit_predictor.add_gap_features' training-time
    definition (cumulative race time difference by position, capped at
    pit_predictor.MAX_GAP_SECONDS).

    Args:
        db: Async DB session.
        session_id: Session to read.
        driver_id: Driver to locate within the field.
    Returns:
        Dict with position, gap_to_car_ahead, gap_to_car_behind,
        target_ahead_driver_id, target_behind_driver_id. The two target ids are
        None for the leader/last car, and all fields fall back to
        MAX_GAP_SECONDS/no-target/back-of-field when driver_id has no
        persisted lap yet in this session (e.g. the very first lap ingested).
    """
    subq = (
        select(LapData.driver_id, func.max(LapData.lap_number).label("max_lap"))
        .where(LapData.session_id == session_id)
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
) -> dict[str, Any]:
    """Resolve circuit/season/round/total_laps/weather/position context for one driver+lap.

    Args:
        db: Async DB session.
        async_redis_client: Async Redis client, for the live weather key.
        session_id: Session the lap belongs to.
        driver_id: Driver to resolve field position/neighbors for.
        compound: Current tyre compound, for the weather DB-average fallback.
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
    position_context = await _resolve_position_context(db, session_id, driver_id)

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
        Prediction fields matching the StrategyPrediction model; undercut_score
        and overcut_score are placeholder 0.0, overwritten by the caller.
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
    fuel_adjusted_time = -tire_deg_model.FUEL_TIME_PENALTY_PER_KG * (
        tire_deg_model.ASSUMED_START_FUEL_KG - fuel_at_lap
    )

    tire_deg_features = [
        [
            lap_number,
            compound_encoded,
            tyre_age_laps,
            fuel_adjusted_time,
            circuit_code,
            driver_code,
        ]
    ]

    # Fallback defaults — used both when a model never loaded (deg_model is
    # None) and when a loaded model raises during inference (corrupt
    # weights, a shape mismatch, etc.): either way the worker must not crash,
    # it must degrade to a null prediction and keep processing subsequent
    # laps/drivers.
    tire_life_remaining = 0.0
    predicted_life_remaining = float(tire_deg_model.MAX_LOOKAHEAD_LAPS)
    if deg_model is not None:
        try:
            # predict() and predict_life_remaining_batch() are treated as one
            # unit (same model, and pit_features below needs both to be
            # mutually consistent) rather than falling back independently.
            with f1_ml_inference_duration_seconds.labels(model="tire_deg").time():
                tire_life_remaining = float(deg_model.predict(tire_deg_features)[0])
            predicted_life_remaining = float(
                tire_deg_model.predict_life_remaining_batch(
                    deg_model,
                    np.array([lap_number]),
                    np.array([compound_encoded]),
                    np.array([tyre_age_laps]),
                    np.array([fuel_adjusted_time]),
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
            tire_life_remaining = 0.0
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
        "optimal_pit_lap": lap_number + max(int(tire_life_remaining), 1),
        "pit_probability": pit_probability,
        "undercut_score": 0.0,
        "overcut_score": 0.0,
        "tire_life_remaining": tire_life_remaining,
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


async def _persist_and_publish(context: dict[str, Any]) -> None:
    models = _load_models()
    maps_cache = _load_encoding_maps()

    session_id = uuid.UUID(str(context["session_id"]))
    driver_id = uuid.UUID(str(context["driver_id"]))
    compound = str(context.get("compound", "")).upper()

    async_redis_client: aioredis.Redis = aioredis.from_url(  # type: ignore[type-arg]
        get_redis_settings().redis_url, decode_responses=True
    )
    session_factory = _get_session_factory()
    try:
        async with session_factory() as db:
            resolved = await _resolve_inference_context(
                db, async_redis_client, session_id, driver_id, compound
            )
            prediction = _run_inference(models, maps_cache, context, resolved, driver_id)
            undercut_score, overcut_score = await _resolve_undercut_overcut(
                async_redis_client, db, resolved, session_id, driver_id
            )
            prediction["undercut_score"] = undercut_score
            prediction["overcut_score"] = overcut_score

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


def _build_plan_explanation(
    race_state: RaceSimulationInput,
    requester_state: DriverRaceState,
    pit_laps: list[int],
    compounds: list[str],
    total_laps: int,
    remaining_laps: int,
) -> dict[str, Any]:
    """Explain why a plan's position_gain_loss came out the way it did.

    drivers_overtaken lists every OTHER driver currently behind the requester
    (higher cumulative_race_time_seconds) whose gap is less than
    race_simulator.PIT_STOP_SECONDS — close enough to leapfrog the requester
    on a full pit-stop time loss. This is a static property of the field's
    gaps at current_lap, computed the same way regardless of whether this
    plan has a forced pit stop — the frontend relabels the same list
    ("overtake you" vs "you overtake") based on position_gain_loss's sign.

    Args:
        race_state: The built field state (post _build_race_state).
        requester_state: race_state.drivers entry for the requesting driver.
        pit_laps, compounds: This plan's forced pit schedule (may be empty).
        total_laps: current_lap + remaining_laps from the request.
        remaining_laps: The request's own remaining_laps — used verbatim only
            when pit_laps is empty (no forced stop to measure "after" from).
    Returns:
        PlanExplanation-shaped dict.
    """
    drivers_overtaken: list[_OvertakingDriverEntry] = sorted(
        (
            _OvertakingDriverEntry(
                position=driver.starting_position,
                driver_id=driver.driver_id,
                gap_seconds=driver.cumulative_race_time_seconds
                - requester_state.cumulative_race_time_seconds,
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
        fresh_tyre_gain_per_lap = _FRESH_TYRE_GAIN_PER_LAP_SECONDS.get(compounds[-1], 0.0)
    else:
        laps_after_pit = remaining_laps
        fresh_tyre_gain_per_lap = 0.0

    return {
        "pit_cost_seconds": race_simulator.PIT_STOP_SECONDS,
        "drivers_overtaken": drivers_overtaken,
        "remaining_laps": laps_after_pit,
        "fresh_tyre_gain_per_lap": fresh_tyre_gain_per_lap,
        "total_recoverable_seconds": fresh_tyre_gain_per_lap * laps_after_pit,
    }


async def _run_simulation(payload: dict[str, Any]) -> dict[str, Any]:
    """Build race state from DB + request, run the Monte Carlo simulation, shape the result.

    Args:
        payload: session_id plus the SimulateStrategyRequest fields (driver_id,
            current_lap, current_compound, current_tyre_age, remaining_laps,
            pit_laps, compounds — the latter two already length-matched and
            compound-validated by SimulateStrategyRequest's model_validator).
    Returns:
        SimulateStrategyResponse-shaped dict (JSON-serialisable).
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

    forced_pit_laps: dict[str, dict[int, tuple[str, int]]] | None = None
    if pit_laps:
        schedule = {
            lap: (compound, _COMPOUND_ENCODING.get(compound, _COMPOUND_ENCODING["MEDIUM"]))
            for lap, compound in zip(pit_laps, compounds, strict=True)
        }
        forced_pit_laps = {str(requesting_driver_id): schedule}

    with f1_ml_inference_duration_seconds.labels(model="race_simulator").time():
        result = race_simulator.simulate_race(
            race_state, tire_deg_pipelines, pit_model, sc_model, forced_pit_laps=forced_pit_laps
        )

    requester_id_str = str(requesting_driver_id)
    requesting_distribution = next(
        d for d in result.driver_distributions if d.driver_id == requester_id_str
    )
    requester_state = next(d for d in race_state.drivers if d.driver_id == requester_id_str)
    position_gain_loss = round(
        requester_state.starting_position - requesting_distribution.mean_position
    )
    explanation = _build_plan_explanation(
        race_state,
        requester_state,
        pit_laps,
        compounds,
        total_laps,
        int(payload["remaining_laps"]),
    )

    return {
        "driver_id": requester_id_str,
        "strategies": [
            {
                "pit_laps": pit_laps,
                "compounds": compounds,
                "predicted_finish_time": requesting_distribution.mean_finish_time_seconds,
                "position_gain_loss": position_gain_loss,
                "confidence_interval": (
                    requesting_distribution.finish_time_p5_seconds,
                    requesting_distribution.finish_time_p95_seconds,
                ),
                "explanation": explanation,
            }
        ],
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
