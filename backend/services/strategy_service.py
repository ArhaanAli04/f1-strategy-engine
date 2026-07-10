"""Pit window, undercut/overcut, and competitor-strategy predictions.

This module deliberately duplicates a small S3 model-loader and the
tire_deg_model/pit_predictor feature-encoding helpers rather than importing
prediction_worker.py or another services/ module — CLAUDE.md forbids
services/ importing other services/, and workers/ must not be imported by
services/ either (that's backwards layering). See pit_predictor.py's
docstring for the same convention already established for services/ml.

Several modelling gaps had to be worked around, all documented at the point
they're used rather than silently papered over:

- circuit_id_encoded/driver_id_encoded: train_models.py's _encode_categoricals
  fits these via pd.Categorical(...).codes fresh per training run and never
  persists the resulting category list (prediction_worker.py has the same
  unresolved gap). There is no way to recover the exact integer code a
  loaded pipeline was actually trained with. _stable_code() below is a
  deterministic, self-consistent stand-in (same id always maps to the same
  code across calls) — not a claim that it matches the training-time
  encoding. compound_encoded uses a hardcoded alphabetical-order mapping
  instead, since {HARD, INTERMEDIATE, MEDIUM, SOFT, WET} is a small, fixed,
  near-certainly-fully-observed set — pd.Categorical's inferred code order
  for it is far more predictable than for circuit/driver IDs.
- total_laps: neither Race nor Session persists race distance. It's
  approximated as MAX(lap_number) observed so far in the session, which
  under-estimates mid-race and converges to the true value near the finish.
- get_competitor_predicted_strategy holds gap_to_car_ahead/behind at
  pit_predictor.MAX_GAP_SECONDS and safety_car_probability at 0.0 — a real
  forward gap/SC model would need telemetry_service (forbidden import) or
  the full race_simulator.py multi-driver simulation, which is out of scope
  for a per-competitor pit-lap estimate.
- Model S3 tag: downloads use the "latest" tag, matching prediction_worker.py's
  existing convention exactly. train_models.py currently only ever writes to
  a timestamped tag and conditionally to "production" — never "latest" — so
  this (like prediction_worker.py) will 404 until that's reconciled. Not
  fixed here since it's a pre-existing Day 6/7 inconsistency, not something
  introduced today.
- track_temp/air_temp: tire_deg pipelines now require these two features
  (see tire_deg_model.FEATURE_COLUMNS). _resolve_weather() prefers the live
  f1:{season}:{round}:weather:latest Redis key (written by
  ingest_live_session.py); when it's absent (pre-race, or a historical
  session with no live ingestor ever run) it falls back to a live DB query
  averaging lap_data.track_temp/air_temp for the same circuit+compound —
  the closest inference-time equivalent of add_engineered_features's
  training-time group-mean imputation, since the fitted pipeline itself
  has no memory of the training data's per-circuit averages.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
import zlib
from pathlib import Path
from typing import Any

import boto3
import joblib
import numpy as np
import redis.asyncio as aioredis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import get_aws_settings, get_ml_settings
from backend.core.exceptions import ModelNotLoadedError, NotFoundError
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import LapData
from backend.services.cache_service import cacheable
from backend.services.ml import pit_predictor, tire_deg_model
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
_MODEL_VERSION_TAG = "latest"
_COMPOUND_ENCODING = {"HARD": 0, "INTERMEDIATE": 1, "MEDIUM": 2, "SOFT": 3, "WET": 4}

PIT_WINDOW_LOOKAHEAD_LAPS = 15
_STINT2_CANDIDATE_COMPOUNDS = ("SOFT", "MEDIUM", "HARD")
UNDERCUT_PROJECTION_LAPS = 5
UNDERCUT_MONTE_CARLO_SIMS = 200
COMPETITOR_STRATEGY_HORIZON_LAPS = 15

_model_cache: dict[str, Any] = {}


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
    client.download_file(settings.aws_bucket_name, f"{_MODEL_VERSION_TAG}/{filename}", str(path))
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
        _model_cache[filename] = joblib.load(_download_from_s3(filename))
    return _model_cache


def _pipeline_for_compound(models: dict[str, Any], compound: str) -> Any | None:
    """Look up the tire_deg pipeline for a compound, defaulting to MEDIUM's suffix."""
    suffix = _COMPOUND_TO_MODEL_SUFFIX.get(compound, "medium")
    return models.get(f"tire_deg_{suffix}.pkl")


def _stable_code(value: str, modulus: int = 1000) -> int:
    """Deterministic proxy for an unrecoverable training-time pd.Categorical code.

    Args:
        value: The id (circuit_id or driver_id, stringified) to encode.
        modulus: Range to fold the hash into.
    Returns:
        A stable integer in [0, modulus) — see module docstring for why this
        exists instead of the true training-time code.
    """
    return zlib.crc32(value.encode()) % modulus


# --- Shared DB helpers ---


async def _current_state(
    db: AsyncSession, session_id: uuid.UUID, driver_id: uuid.UUID
) -> dict[str, Any]:
    """Latest lap + circuit + estimated total-laps context for one driver in a session.

    Args:
        db: Async DB session.
        session_id: Session to read.
        driver_id: Driver to read.
    Returns:
        Dict with lap_number, compound, tyre_age_laps, position, total_laps, circuit_id.
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
        select(Race.circuit_id)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .where(SessionModel.id == session_id)
    )
    circuit_id = (await db.execute(circuit_query)).scalar_one()

    return {
        "lap_number": lap.lap_number,
        "compound": lap.compound,
        "tyre_age_laps": lap.tyre_age_laps,
        "position": lap.position,
        "total_laps": int(total_laps),
        "circuit_id": circuit_id,
    }


async def _cumulative_race_time(
    db: AsyncSession, session_id: uuid.UUID, driver_id: uuid.UUID, up_to_lap: int
) -> float:
    query = select(func.sum(LapData.lap_time_seconds)).where(
        LapData.session_id == session_id,
        LapData.driver_id == driver_id,
        LapData.lap_number <= up_to_lap,
        LapData.lap_time_seconds.is_not(None),
    )
    return float((await db.execute(query)).scalar_one() or 0.0)


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
    track_temp: float,
    air_temp: float,
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
        track_temp, air_temp: Held constant across the projected stint (see
            _resolve_weather) — short-term weather drift over a stint's length
            is a second-order effect next to tyre wear.
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
            np.full(n_laps, track_temp),
            np.full(n_laps, air_temp),
        ]
    )
    result: float = float(pipeline.predict(features).sum())
    return result


def _sampled_stint_delta(
    rng: np.random.Generator,
    pipeline: Any,
    compound_encoded: int,
    driver_code: int,
    circuit_code: int,
    start_lap: int,
    n_laps: int,
    start_tyre_age: int,
    total_laps: int,
    track_temp: float,
    air_temp: float,
) -> float:
    """One Monte Carlo draw of a stint's total time delta.

    Deterministic tire_deg prediction plus Gaussian noise whose variance scales
    with n_laps (the sum of n_laps iid per-lap noise terms), reusing
    race_simulator.LAP_TIME_NOISE_STD_SECONDS for consistency with the Day 8
    simulator's noise assumption.

    Args: see _project_stint_delta; rng is the caller's shared Generator.
    Returns:
        Sampled total delta in seconds; 0.0 if n_laps <= 0.
    """
    if n_laps <= 0:
        return 0.0
    deterministic = _project_stint_delta(
        pipeline,
        compound_encoded,
        driver_code,
        circuit_code,
        start_lap,
        n_laps,
        start_tyre_age,
        total_laps,
        track_temp,
        air_temp,
    )
    noise = float(rng.normal(0.0, LAP_TIME_NOISE_STD_SECONDS * math.sqrt(n_laps)))
    return deterministic + noise


# --- get_optimal_pit_window ---


def _key_pit_window(
    client: aioredis.Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    driver_id: uuid.UUID,
) -> str:
    return f"f1:{season}:{round_number}:strategy:{driver_id}:pit_window"


@cacheable(ttl=30, key_fn=_key_pit_window)
async def get_optimal_pit_window(
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
    stint-from-pit-lap-to-race-end delta.

    Args:
        client: Redis client (cache-aside — first positional arg per cacheable's contract).
        db: Async DB session.
        season, round_number: Race weekend identifiers, for the cache key.
        session_id: Session to evaluate.
        driver_id: Driver to plan a pit window for.
    Returns:
        Up to 3 dicts (pit_lap, window_start, window_end, projected_total_delta_seconds),
        ascending by projected_total_delta_seconds (lower = better).
    """
    models = _load_models()
    state = await _current_state(db, session_id, driver_id)
    driver_code = _stable_code(str(driver_id))
    circuit_code = _stable_code(str(state["circuit_id"]))
    current_compound_encoded = _COMPOUND_ENCODING.get(
        state["compound"], _COMPOUND_ENCODING["MEDIUM"]
    )
    current_pipeline = _pipeline_for_compound(models, state["compound"])
    if current_pipeline is None:
        raise ModelNotLoadedError(
            f"No tire degradation model loaded for compound {state['compound']}"
        )

    current_track_temp, current_air_temp = await _resolve_weather(
        client, db, season, round_number, state["circuit_id"], state["compound"]
    )
    candidate_weather = {
        candidate_compound: await _resolve_weather(
            client, db, season, round_number, state["circuit_id"], candidate_compound
        )
        for candidate_compound in _STINT2_CANDIDATE_COMPOUNDS
    }

    candidates: list[dict[str, Any]] = []
    max_pit_lap = min(state["lap_number"] + PIT_WINDOW_LOOKAHEAD_LAPS, state["total_laps"])
    for pit_lap in range(state["lap_number"] + 1, max_pit_lap + 1):
        laps_on_current = pit_lap - state["lap_number"]
        stint1_delta = _project_stint_delta(
            current_pipeline,
            current_compound_encoded,
            driver_code,
            circuit_code,
            state["lap_number"] + 1,
            laps_on_current,
            state["tyre_age_laps"],
            state["total_laps"],
            current_track_temp,
            current_air_temp,
        )

        best_stint2_delta: float | None = None
        for candidate_compound in _STINT2_CANDIDATE_COMPOUNDS:
            pipeline = _pipeline_for_compound(models, candidate_compound)
            if pipeline is None:
                continue
            laps_remaining = state["total_laps"] - pit_lap
            candidate_track_temp, candidate_air_temp = candidate_weather[candidate_compound]
            delta = _project_stint_delta(
                pipeline,
                _COMPOUND_ENCODING[candidate_compound],
                driver_code,
                circuit_code,
                pit_lap + 1,
                laps_remaining,
                0,
                state["total_laps"],
                candidate_track_temp,
                candidate_air_temp,
            )
            if best_stint2_delta is None or delta < best_stint2_delta:
                best_stint2_delta = delta

        total_delta = stint1_delta + PIT_STOP_SECONDS + (best_stint2_delta or 0.0)
        candidates.append(
            {
                "pit_lap": pit_lap,
                "window_start": state["lap_number"] + 1,
                "window_end": max_pit_lap,
                "projected_total_delta_seconds": total_delta,
            }
        )

    candidates.sort(key=lambda c: c["projected_total_delta_seconds"])
    return candidates[:3]


# --- get_undercut_score / get_overcut_score ---


async def _undercut_overcut_probability(
    client: aioredis.Redis,  # type: ignore[type-arg]
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
        client: Redis client, for _resolve_weather.
        db: Async DB session.
        season, round_number: Race weekend identifiers, for _resolve_weather.
        session_id: Session to evaluate.
        pitting_now_driver_id: Driver assumed to pit this lap.
        pitting_next_lap_driver_id: Driver assumed to pit next lap.
    Returns:
        Dict with probability_pit_now_gains_position, projected_gap_seconds (mean
        over sims; positive = pitting_now_driver_id ends up ahead), n_laps_projected.
    """
    models = _load_models()
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

    now_code = _stable_code(str(pitting_now_driver_id))
    next_code = _stable_code(str(pitting_next_lap_driver_id))
    now_circuit_code = _stable_code(str(now_state["circuit_id"]))
    next_circuit_code = _stable_code(str(next_state["circuit_id"]))
    default_compound_code = _COMPOUND_ENCODING["MEDIUM"]
    now_compound_encoded = _COMPOUND_ENCODING.get(now_state["compound"], default_compound_code)
    next_compound_encoded = _COMPOUND_ENCODING.get(next_state["compound"], default_compound_code)

    now_track_temp, now_air_temp = await _resolve_weather(
        client, db, season, round_number, now_state["circuit_id"], now_state["compound"]
    )
    next_track_temp, next_air_temp = await _resolve_weather(
        client, db, season, round_number, next_state["circuit_id"], next_state["compound"]
    )

    rng = np.random.default_rng()
    wins = 0
    gap_samples = np.empty(UNDERCUT_MONTE_CARLO_SIMS)
    for i in range(UNDERCUT_MONTE_CARLO_SIMS):
        now_delta = PIT_STOP_SECONDS + _sampled_stint_delta(
            rng,
            now_pipeline,
            now_compound_encoded,
            now_code,
            now_circuit_code,
            now_state["lap_number"] + 1,
            UNDERCUT_PROJECTION_LAPS,
            0,
            now_state["total_laps"],
            now_track_temp,
            now_air_temp,
        )
        stay_out_delta = _sampled_stint_delta(
            rng,
            next_pipeline,
            next_compound_encoded,
            next_code,
            next_circuit_code,
            next_state["lap_number"] + 1,
            1,
            next_state["tyre_age_laps"],
            next_state["total_laps"],
            next_track_temp,
            next_air_temp,
        )
        fresh_delta = PIT_STOP_SECONDS + _sampled_stint_delta(
            rng,
            next_pipeline,
            next_compound_encoded,
            next_code,
            next_circuit_code,
            next_state["lap_number"] + 2,
            UNDERCUT_PROJECTION_LAPS - 1,
            0,
            next_state["total_laps"],
            next_track_temp,
            next_air_temp,
        )
        next_delta = stay_out_delta + fresh_delta

        new_deficit = deficit + now_delta - next_delta
        gap_samples[i] = -new_deficit
        if new_deficit < 0:
            wins += 1

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
        See _undercut_overcut_probability, plus target_driver_id.
    """
    result = await _undercut_overcut_probability(
        client, db, season, round_number, session_id, driver_id, target_driver_id
    )
    return {"target_driver_id": str(target_driver_id), **result}


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
        client, db, season, round_number, session_id, target_driver_id, driver_id
    )
    return {
        "target_driver_id": str(target_driver_id),
        "probability_stay_out_retains_position": 1.0 - result["probability_pit_now_gains_position"],
        "projected_gap_seconds": -result["projected_gap_seconds"],
        "n_laps_projected": result["n_laps_projected"],
    }


# --- get_competitor_predicted_strategy ---


def _first_pit_lap_over_threshold(
    pit_model: Any,
    tire_deg_pipeline: Any | None,
    compound_encoded: int,
    driver_code: int,
    circuit_code: int,
    current_lap: int,
    tyre_age_laps: int,
    position: int,
    gap_to_ahead: float,
    gap_to_behind: float,
    safety_car_probability: float,
    total_laps: int,
    track_temp: float,
    air_temp: float,
) -> tuple[int, float]:
    """Roll pit_predictor forward lap-by-lap until it crosses pit_predictor.ALERT_THRESHOLD.

    gap_to_ahead/behind, safety_car_probability, and track_temp/air_temp are held
    constant at the caller-supplied values — see module docstring for why no
    forward gap/SC model is available here, and _resolve_weather for weather.

    Args: see pit_predictor.FEATURE_COLUMNS for feature semantics.
    Returns:
        (predicted_pit_lap, pit_probability_at_that_lap). If the threshold is
        never crossed within the horizon, returns the horizon's last lap and
        its probability.
    """
    horizon = min(COMPETITOR_STRATEGY_HORIZON_LAPS, max(total_laps - current_lap, 1))
    last_lap, last_prob = current_lap, 0.0
    for offset in range(1, horizon + 1):
        lap_number = current_lap + offset
        future_tyre_age = tyre_age_laps + offset
        life_remaining = float(tire_deg_model.MAX_LOOKAHEAD_LAPS)
        if tire_deg_pipeline is not None:
            life_remaining = float(
                tire_deg_model.predict_life_remaining_batch(
                    tire_deg_pipeline,
                    np.array([lap_number]),
                    np.array([compound_encoded]),
                    np.array([future_tyre_age]),
                    np.array([0.0]),
                    np.array([circuit_code]),
                    np.array([driver_code]),
                    np.array([track_temp]),
                    np.array([air_temp]),
                )[0]
            )
        fuel_load_est = max(
            tire_deg_model.ASSUMED_START_FUEL_KG * (1 - lap_number / max(total_laps, 1)), 0.0
        )
        features = np.array(
            [
                [
                    future_tyre_age,
                    life_remaining,
                    gap_to_ahead,
                    gap_to_behind,
                    safety_car_probability,
                    total_laps - lap_number,
                    position,
                    fuel_load_est,
                ]
            ]
        )
        probability = float(pit_model.predict_proba(features)[0][1])
        last_lap, last_prob = lap_number, probability
        if probability >= pit_predictor.ALERT_THRESHOLD:
            return lap_number, probability
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
        select(Race.circuit_id)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .where(SessionModel.id == session_id)
    )
    circuit_id = (await db.execute(circuit_query)).scalar_one()
    circuit_code = _stable_code(str(circuit_id))

    results: list[dict[str, Any]] = []
    for lap in latest_laps:
        compound_encoded = _COMPOUND_ENCODING.get(lap.compound, _COMPOUND_ENCODING["MEDIUM"])
        tire_deg_pipeline = _pipeline_for_compound(models, lap.compound)
        track_temp, air_temp = await _resolve_weather(
            client, db, season, round_number, circuit_id, lap.compound
        )
        predicted_lap, probability = _first_pit_lap_over_threshold(
            pit_model,
            tire_deg_pipeline,
            compound_encoded,
            _stable_code(str(lap.driver_id)),
            circuit_code,
            lap.lap_number,
            lap.tyre_age_laps,
            lap.position or len(latest_laps),
            pit_predictor.MAX_GAP_SECONDS,
            pit_predictor.MAX_GAP_SECONDS,
            0.0,
            total_laps,
            track_temp,
            air_temp,
        )
        results.append(
            {
                "driver_id": str(lap.driver_id),
                "predicted_pit_lap": predicted_lap,
                "pit_probability": probability,
            }
        )
    return results
