"""Celery task that runs the strategy ML models and persists + publishes predictions."""

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import joblib
import redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_aws_settings, get_ml_settings, get_redis_settings
from backend.core.database import get_engine
from backend.models.strategy import StrategyPrediction
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
_MODEL_VERSION_TAG = "latest"

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


def _run_inference(models: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Run the strategy models for one driver/lap context.

    Undercut/overcut/confidence scoring depends on opponent-relative
    simulation logic that lands with the ML models themselves (Day 7+);
    those fields are placeholder zeros until then.

    Args:
        models: Loaded model registry, keyed by filename.
        context: Driver + lap context — expects compound, tyre_age_laps, lap_number.
    Returns:
        Prediction fields matching the StrategyPrediction model.
    """
    suffix = _COMPOUND_TO_MODEL_SUFFIX.get(str(context.get("compound", "")).upper(), "medium")
    deg_model = models.get(f"tire_deg_{suffix}.pkl")
    pit_model = models.get("pit_predictor.pkl")
    features = [[context.get("tyre_age_laps", 0), context.get("lap_number", 0)]]

    tire_life_remaining = float(deg_model.predict(features)[0]) if deg_model is not None else 0.0
    pit_probability = (
        float(pit_model.predict_proba(features)[0][1]) if pit_model is not None else 0.0
    )

    return {
        "optimal_pit_lap": int(context.get("lap_number", 0)) + max(int(tire_life_remaining), 1),
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
    prediction = _run_inference(models, context)

    session_id = uuid.UUID(str(context["session_id"]))
    driver_id = uuid.UUID(str(context["driver_id"]))

    row = StrategyPrediction(
        id=uuid.uuid4(),
        session_id=session_id,
        driver_id=driver_id,
        predicted_at=datetime.now(UTC),
        **prediction,
    )

    session_factory = _get_session_factory()
    async with session_factory() as db:
        db.add(row)
        await db.commit()

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
