"""Celery task that runs the strategy ML models and persists + publishes predictions."""

import asyncio
import json
import logging
import uuid
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import joblib
import redis
import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_aws_settings, get_ml_settings, get_redis_settings
from backend.core.database import get_engine
from backend.core.metrics import (
    f1_ml_inference_duration_seconds,
    f1_strategy_predictions_total,
)
from backend.models.race import Circuit, Race
from backend.models.race import Session as SessionModel
from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData
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

# Same encoding convention as strategy_service.py's identical constant —
# duplicated rather than imported (services/ must not be imported by workers/
# either, mirroring strategy_service.py's own "no cross-service imports" rule).
_COMPOUND_ENCODING = {"HARD": 0, "INTERMEDIATE": 1, "MEDIUM": 2, "SOFT": 3, "WET": 4}

_model_cache: dict[str, Any] = {}
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
    client = boto3.client("s3", region_name=settings.aws_region)
    key = f"{_MODEL_VERSION_TAG}/{filename}"
    client.download_file(settings.aws_bucket_name, key, str(path))
    return path


def _load_models() -> dict[str, Any]:
    """Load all registry models into an in-process cache, downloading from S3 on first use.

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
    return _model_cache


def _stable_code(value: str, modulus: int = 1000) -> int:
    """Deterministic proxy for an unrecoverable training-time pd.Categorical code.

    Duplicated from strategy_service.py's identical helper (same rationale: the
    exact training-time circuit/driver category code can't be recovered — see
    that module's docstring). Kept in sync by convention, not by import.

    Args:
        value: The id (circuit_id or driver_id, stringified) to encode.
        modulus: Range to fold the hash into.
    Returns:
        A stable integer in [0, modulus).
    """
    return zlib.crc32(value.encode()) % modulus


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
    no-cross-service-import reason as _stable_code above.

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


async def _resolve_inference_context(
    db: AsyncSession,
    async_redis_client: aioredis.Redis,  # type: ignore[type-arg]
    session_id: uuid.UUID,
    compound: str,
) -> dict[str, Any]:
    """Resolve circuit/season/round/total_laps/weather context absent from the raw lap dict.

    Args:
        db: Async DB session.
        async_redis_client: Async Redis client, for the live weather key.
        session_id: Session the lap belongs to.
        compound: Current tyre compound, for the weather DB-average fallback.
    Returns:
        Dict with circuit_id, total_laps, track_temp, air_temp.
    """
    context_query = (
        select(Race.circuit_id, Race.season, Race.round_number)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .where(SessionModel.id == session_id)
    )
    circuit_id, season, round_number = (await db.execute(context_query)).one()

    total_laps_query = select(func.max(LapData.lap_number)).where(LapData.session_id == session_id)
    total_laps = (await db.execute(total_laps_query)).scalar_one()

    track_temp, air_temp = await _resolve_weather(
        async_redis_client, db, season, round_number, circuit_id, compound
    )
    return {
        "circuit_id": circuit_id,
        "total_laps": int(total_laps) if total_laps is not None else None,
        "track_temp": track_temp,
        "air_temp": air_temp,
    }


def _run_inference(
    models: dict[str, Any],
    context: dict[str, Any],
    resolved: dict[str, Any],
    driver_id: uuid.UUID,
) -> dict[str, Any]:
    """Run the strategy models for one driver/lap context.

    tire_deg inference now uses the full 8-column feature vector
    (tire_deg_model.FEATURE_COLUMNS) — previously only lap_number and
    tyre_age_laps were passed, a 2-vs-8 mismatch predating the weather-features
    pass (see CLAUDE.md's Deferred Wiring notes). pit_predictor inference is
    NOT fixed here — it needs an entirely different feature set
    (pit_predictor.FEATURE_COLUMNS: gaps, safety_car_probability, position,
    fuel_load_est) that this worker doesn't have inputs for yet, and that gap
    is already tracked in CLAUDE.md bundled with the undercut_score/
    overcut_score wiring, out of scope for today's fix.

    Undercut/overcut/confidence scoring depends on opponent-relative
    simulation logic that lands with the ML models themselves (Day 7+);
    those fields are placeholder zeros until then.

    Args:
        models: Loaded model registry, keyed by filename.
        context: Driver + lap context — expects compound, tyre_age_laps, lap_number.
        resolved: Output of _resolve_inference_context — circuit_id, total_laps,
            track_temp, air_temp.
        driver_id: Driver this prediction is for, for the driver_id_encoded feature.
    Returns:
        Prediction fields matching the StrategyPrediction model.
    """
    compound = str(context.get("compound", "")).upper()
    suffix = _COMPOUND_TO_MODEL_SUFFIX.get(compound, "medium")
    deg_model = models.get(f"tire_deg_{suffix}.pkl")
    pit_model = models.get("pit_predictor.pkl")

    lap_number = int(context.get("lap_number", 0))
    tyre_age_laps = int(context.get("tyre_age_laps", 0))
    total_laps = resolved["total_laps"] or lap_number
    compound_encoded = _COMPOUND_ENCODING.get(compound, _COMPOUND_ENCODING["MEDIUM"])
    circuit_code = _stable_code(str(resolved["circuit_id"]))
    driver_code = _stable_code(str(driver_id))

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
            resolved["track_temp"],
            resolved["air_temp"],
        ]
    ]
    # Unchanged pre-existing behaviour: pit_predictor.FEATURE_COLUMNS is a
    # different 8-column schema this worker can't populate yet (see docstring
    # above) — kept on its own original 2-value vector rather than being fed
    # tire_deg's features, which would be a different, more misleading bug.
    pit_features = [[tyre_age_laps, lap_number]]

    if deg_model is not None:
        with f1_ml_inference_duration_seconds.labels(model="tire_deg").time():
            tire_life_remaining = float(deg_model.predict(tire_deg_features)[0])
    else:
        tire_life_remaining = 0.0

    if pit_model is not None:
        with f1_ml_inference_duration_seconds.labels(model="pit_predictor").time():
            pit_probability = float(pit_model.predict_proba(pit_features)[0][1])
    else:
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


async def _persist_and_publish(context: dict[str, Any]) -> None:
    models = _load_models()

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
                db, async_redis_client, session_id, compound
            )
            prediction = _run_inference(models, context, resolved, driver_id)

            row = StrategyPrediction(
                id=uuid.uuid4(),
                session_id=session_id,
                driver_id=driver_id,
                predicted_at=datetime.now(UTC),
                **prediction,
            )
            db.add(row)
            await db.commit()
            f1_strategy_predictions_total.inc()
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

_WET_COMPOUNDS = frozenset({"INTERMEDIATE", "WET"})


async def _cumulative_race_time(
    db: AsyncSession, session_id: uuid.UUID, driver_id: uuid.UUID, up_to_lap: int
) -> float:
    """Sum of lap_time_seconds up to and including up_to_lap for one driver.

    Duplicated from strategy_service._cumulative_race_time — same no-cross-
    service-import reason as _stable_code/_resolve_weather above.

    Args:
        db: Async DB session.
        session_id: Session to query.
        driver_id: Driver to query.
        up_to_lap: Last lap number (inclusive) to sum.
    Returns:
        Cumulative elapsed race time in seconds; 0.0 if no laps recorded yet.
    """
    query = select(func.sum(LapData.lap_time_seconds)).where(
        LapData.session_id == session_id,
        LapData.driver_id == driver_id,
        LapData.lap_number <= up_to_lap,
        LapData.lap_time_seconds.is_not(None),
    )
    return float((await db.execute(query)).scalar_one() or 0.0)


async def _build_race_state(
    db: AsyncSession,
    async_redis_client: aioredis.Redis,  # type: ignore[type-arg]
    session_id: uuid.UUID,
    requesting_driver_id: uuid.UUID,
    current_lap: int,
    current_compound: str,
    current_tyre_age: int,
    total_laps: int,
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

    drivers: list[DriverRaceState] = []
    requesting_driver_found = False
    for lap in latest_laps:
        driver_id_str = str(lap.driver_id)
        if lap.driver_id == requesting_driver_id:
            requesting_driver_found = True
            compound, tyre_age_laps, up_to_lap = current_compound, current_tyre_age, current_lap
        else:
            compound, tyre_age_laps, up_to_lap = lap.compound, lap.tyre_age_laps, lap.lap_number
        cumulative_time = await _cumulative_race_time(db, session_id, lap.driver_id, up_to_lap)
        drivers.append(
            DriverRaceState(
                driver_id=driver_id_str,
                starting_position=lap.position or len(latest_laps),
                compound=compound,
                compound_encoded=_COMPOUND_ENCODING.get(compound, _COMPOUND_ENCODING["MEDIUM"]),
                tyre_age_laps=tyre_age_laps,
                driver_id_encoded=_stable_code(driver_id_str),
                cumulative_race_time_seconds=cumulative_time,
            )
        )

    if not requesting_driver_found:
        # No persisted lap data yet for the requester (e.g. pre-race what-if) —
        # their request fields are the only state available; race starts fresh.
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
                driver_id_encoded=_stable_code(driver_id_str),
                cumulative_race_time_seconds=0.0,
            )
        )

    return RaceSimulationInput(
        circuit_name=circuit_name,
        circuit_id_encoded=_stable_code(str(circuit_id)),
        current_lap=current_lap,
        total_laps=total_laps,
        wet_track=current_compound in _WET_COMPOUNDS,
        track_temp=track_temp,
        air_temp=air_temp,
        drivers=drivers,
    )


async def _run_simulation(payload: dict[str, Any]) -> dict[str, Any]:
    """Build race state from DB + request, run the Monte Carlo simulation, shape the result.

    Args:
        payload: session_id plus the SimulateStrategyRequest fields (driver_id,
            current_lap, current_compound, current_tyre_age, remaining_laps,
            pit_laps, compounds — the latter two already length-matched and
            compound-validated by SimulateStrategyRequest's model_validator).
    Returns:
        SimulateStrategyResponse-shaped dict (JSON-serialisable).
    """
    models = _load_models()
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
            race_state = await _build_race_state(
                db,
                async_redis_client,
                session_id,
                requesting_driver_id,
                current_lap,
                current_compound,
                current_tyre_age,
                total_laps,
            )
    finally:
        await async_redis_client.aclose()  # type: ignore[attr-defined]

    # See telemetry_worker._persist_lap for why this dispose is required.
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
