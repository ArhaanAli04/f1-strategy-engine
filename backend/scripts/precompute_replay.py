"""Precompute everything a Demo Replay shows, so production can play it back without a worker.

Runs the real prediction code once per curated lap window, offline, and
stores the results; the playback process (Day 4 of
docs/internal/demo-deployment-plan-2026.md) then republishes them on a
real-time clock.

For each curated session (or one given with --session-id), lap by lap:
publish that lap's gap snapshot to Redis, run prediction_worker.
compute_prediction for every driver who completed the lap (the same code a
live race runs, without Celery), then find that lap's undercut alerts from
those predictions (alert_service.find_undercut_threats_at_lap). Everything is
computed first and written last, in ONE transaction per session that
replaces the session's old predictions and replay rows — so a run that fails
part-way leaves the previous data untouched.

Writes to whichever database DATABASE_URL points at (same convention as the
backfill scripts), and uses REDIS_URL for the gap snapshots and the
prediction code's own caches.

Run via:
    python -m backend.scripts.precompute_replay --dry-run
    python -m backend.scripts.precompute_replay
    python -m backend.scripts.precompute_replay --session-id <uuid>

Times are seconds on FastF1's session clock (Lap.LapStartTime / Lap.Time) —
the same clock driver_positions is anchored to (see replay_pipeline.py's
_build_position_timeline), so playback can line laps, gaps and circuit-map
positions up on one timeline.
"""

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import boto3
import fastf1
import pandas as pd
import redis.asyncio as aioredis
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_aws_settings, get_redis_settings
from backend.core.database import get_engine
from backend.core.exceptions import F1StrategyError
from backend.models.driver import Driver
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.replay import (
    ReplayAlertEvent,
    ReplayCarNumber,
    ReplayGapSnapshot,
    ReplayLapTiming,
)
from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData
from backend.scripts._ingest_common import resolve_car_numbers
from backend.scripts.replay_pipeline import _compute_lap_gaps, _load_fastf1_session
from backend.services import alert_service
from backend.services.alert_service import LapUndercutAlert
from backend.services.demo_service import CURATED_RACES, DEMO_REPLAY_STATE_KEY
from backend.workers import prediction_worker

logger = logging.getLogger(__name__)

# Matches replay_pipeline's own gaps-key TTL; the key is deleted when the run
# ends anyway, this only bounds a crashed run's leftover.
_GAPS_KEY_TTL_SECONDS = 600


@dataclass(frozen=True)
class LapTiming:
    """When one driver started and finished one lap (a replay_lap_timings row)."""

    driver_id: uuid.UUID
    lap_number: int
    lap_start_seconds: float | None
    lap_end_seconds: float | None


@dataclass(frozen=True)
class CarNumber:
    """One driver's car number in the race (a replay_car_numbers row)."""

    driver_id: uuid.UUID
    car_number: str


def _session_seconds(value: Any) -> float | None:
    """A FastF1 session-clock Timedelta in seconds; None for NaT (no time recorded)."""
    if pd.isna(value):
        return None
    return float(value.total_seconds())


def extract_lap_timings(
    laps: pd.DataFrame,
    code_to_driver_id: dict[str, uuid.UUID],
    start_lap: int,
    end_lap: int,
) -> list[LapTiming]:
    """Start and end time of every driver's laps in the window.

    Playback emits each driver's lap-completion event at lap_end_seconds, the
    moment they really crossed the line, instead of today's fixed-interval
    dispatch.

    Args:
        laps: A loaded FastF1 session's laps (Driver, LapNumber, LapStartTime, Time).
        code_to_driver_id: Driver.code -> Driver.id; laps of any other driver
            are skipped.
        start_lap, end_lap: Inclusive lap window.
    Returns:
        One LapTiming per (driver, lap) in the window, ordered by lap then
        driver code. A time FastF1 did not record (e.g. a car that stopped
        mid-lap) is None.
    """
    in_window = laps[(laps["LapNumber"] >= start_lap) & (laps["LapNumber"] <= end_lap)]
    timings: list[LapTiming] = []
    for row in in_window.sort_values(["LapNumber", "Driver"]).itertuples():
        driver_id = code_to_driver_id.get(row.Driver)
        if driver_id is None:
            continue
        timings.append(
            LapTiming(
                driver_id=driver_id,
                lap_number=int(row.LapNumber),
                lap_start_seconds=_session_seconds(row.LapStartTime),
                lap_end_seconds=_session_seconds(row.Time),
            )
        )
    return timings


def build_gap_snapshots(
    fastf1_session: fastf1.core.Session,
    code_to_driver_id: dict[str, uuid.UUID],
    session_id: uuid.UUID,
    start_lap: int,
    end_lap: int,
) -> dict[int, dict[str, Any]]:
    """The field's order and gaps at the end of each lap, from FastF1's own timing.

    Uses replay_pipeline._compute_lap_gaps unchanged, so the stored payload is
    exactly what a replay publishes today. Starts one lap before the window:
    at the window's first moment the timing tower should show the field as it
    stood after the previous lap.

    Args:
        fastf1_session: A loaded FastF1 session (laps=True).
        code_to_driver_id: Driver.code -> Driver.id.
        session_id: Session being precomputed, for the payload's session_id.
        start_lap, end_lap: Inclusive lap window.
    Returns:
        lap_number -> SessionGapsResponse-shaped payload. A lap with no usable
        Time/Position rows is absent.
    """
    snapshots: dict[int, dict[str, Any]] = {}
    for lap_number in range(max(start_lap - 1, 1), end_lap + 1):
        payload = _compute_lap_gaps(fastf1_session, lap_number, code_to_driver_id, session_id)
        if payload is not None:
            snapshots[lap_number] = payload
    return snapshots


def extract_car_numbers(
    fastf1_session: fastf1.core.Session, code_to_driver_id: dict[str, uuid.UUID]
) -> list[CarNumber]:
    """Each known driver's car number in this race.

    Args:
        fastf1_session: A loaded FastF1 session.
        code_to_driver_id: Driver.code -> Driver.id.
    Returns:
        One CarNumber per driver FastF1 could resolve and we know, ordered by
        car number.
    """
    car_numbers = resolve_car_numbers(fastf1_session, code_to_driver_id)
    return [
        CarNumber(driver_id=driver_id, car_number=car_number)
        for car_number, driver_id in sorted(car_numbers.items(), key=lambda item: int(item[0]))
    ]


# --- The run -------------------------------------------------------------


@dataclass(frozen=True)
class CuratedTarget:
    """One curated race to precompute, as found in the target database."""

    session_id: uuid.UUID
    race_name: str
    season: int
    round_number: int
    start_lap: int
    end_lap: int


@dataclass
class SessionPrecompute:
    """Everything computed for one session, held in memory until the write."""

    target: CuratedTarget
    predictions: list[tuple[uuid.UUID, int, dict[str, Any]]] = field(default_factory=list)
    alerts: list[LapUndercutAlert] = field(default_factory=list)
    lap_timings: list[LapTiming] = field(default_factory=list)
    gap_snapshots: dict[int, dict[str, Any]] = field(default_factory=dict)
    car_numbers: list[CarNumber] = field(default_factory=list)
    failed_predictions: int = 0
    seconds: float = 0.0


async def resolve_targets(db: AsyncSession, session_id: uuid.UUID | None) -> list[CuratedTarget]:
    """The curated races present in this database, optionally just one session.

    Queried directly, deliberately not through demo_service's Redis-cached
    lookup: that cache key is not per-database, so running against Supabase
    with a local Redis could hand back the local database's session ids.

    Args:
        db: Async DB session.
        session_id: Restrict to this session, or None for every curated race.
    Returns:
        One CuratedTarget per curated race found, in CURATED_RACES order.
    Raises:
        ValueError: session_id is given but is not a curated race's session here.
    """
    query = (
        select(Race.season, Race.round_number, SessionModel.id)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .where(
            SessionModel.session_type == "R",
            tuple_(Race.season, Race.round_number).in_(
                [(race.season, race.round_number) for race in CURATED_RACES]
            ),
        )
    )
    found = {(season, rnd): sid for season, rnd, sid in (await db.execute(query)).all()}
    targets = [
        CuratedTarget(
            session_id=found[(race.season, race.round_number)],
            race_name=race.race_name,
            season=race.season,
            round_number=race.round_number,
            start_lap=race.start_lap,
            end_lap=race.end_lap,
        )
        for race in CURATED_RACES
        if (race.season, race.round_number) in found
    ]
    if session_id is None:
        return targets
    chosen = [target for target in targets if target.session_id == session_id]
    if not chosen:
        raise ValueError(f"Session {session_id} is not a curated demo race in this database")
    return chosen


async def _driver_code_map(db: AsyncSession) -> dict[str, uuid.UUID]:
    rows = (await db.execute(select(Driver.code, Driver.id))).all()
    return {row.code: row.id for row in rows}


async def _laps_in_window(db: AsyncSession, target: CuratedTarget) -> dict[int, list[LapData]]:
    query = (
        select(LapData)
        .where(
            LapData.session_id == target.session_id,
            LapData.lap_number >= target.start_lap,
            LapData.lap_number <= target.end_lap,
        )
        .order_by(LapData.lap_number, LapData.driver_id)
    )
    by_lap: dict[int, list[LapData]] = {}
    for lap in (await db.execute(query)).scalars().all():
        by_lap.setdefault(lap.lap_number, []).append(lap)
    return by_lap


def _prediction_context(lap: LapData) -> dict[str, Any]:
    """The same raw_lap shape the live ingestor dispatches to run_strategy_prediction."""
    return {
        "session_id": str(lap.session_id),
        "driver_id": str(lap.driver_id),
        "lap_number": lap.lap_number,
        "lap_time_seconds": lap.lap_time_seconds,
        "compound": lap.compound,
        "tyre_age_laps": lap.tyre_age_laps,
        "is_valid": lap.is_valid,
        "sector1_seconds": lap.sector1_seconds,
        "sector2_seconds": lap.sector2_seconds,
        "sector3_seconds": lap.sector3_seconds,
    }


async def compute_session(
    db: AsyncSession,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    target: CuratedTarget,
    fastf1_session: fastf1.core.Session,
    code_to_driver_id: dict[str, uuid.UUID],
) -> SessionPrecompute:
    """Compute one session's predictions, alerts, timings, gaps and car numbers.

    Writes nothing to the database. Per lap, the lap's gap snapshot is
    published to f1:{season}:{round}:gaps BEFORE that lap's predictions run,
    so any prediction that falls back to that key reads lap N's field, never
    whatever was there before.

    A prediction that fails with a known error (F1StrategyError, e.g. missing
    lap data or model; ValueError, e.g. a bad feature vector) is logged and
    counted, and that driver-lap is left without a prediction. Anything else
    propagates and aborts the run before any write.

    Args:
        db: Async DB session (read-only here).
        redis_client: Async Redis client.
        target: The curated race to compute.
        fastf1_session: The race's loaded FastF1 session.
        code_to_driver_id: Driver.code -> Driver.id.
    Returns:
        The SessionPrecompute, ready for write_session.
    """
    started = time.monotonic()
    result = SessionPrecompute(target=target)
    result.lap_timings = extract_lap_timings(
        fastf1_session.laps, code_to_driver_id, target.start_lap, target.end_lap
    )
    result.gap_snapshots = build_gap_snapshots(
        fastf1_session, code_to_driver_id, target.session_id, target.start_lap, target.end_lap
    )
    result.car_numbers = extract_car_numbers(fastf1_session, code_to_driver_id)
    gaps_key = f"f1:{target.season}:{target.round_number}:gaps"

    laps_by_number = await _laps_in_window(db, target)
    for lap_number in range(target.start_lap, target.end_lap + 1):
        snapshot = result.gap_snapshots.get(lap_number)
        if snapshot is not None:
            await redis_client.setex(gaps_key, _GAPS_KEY_TTL_SECONDS, json.dumps(snapshot))

        scores: dict[uuid.UUID, float] = {}
        for lap in laps_by_number.get(lap_number, []):
            try:
                prediction = await prediction_worker.compute_prediction(
                    db, redis_client, _prediction_context(lap)
                )
            except (F1StrategyError, ValueError):
                logger.warning(
                    "%s lap %d: prediction failed for driver %s",
                    target.race_name,
                    lap_number,
                    lap.driver_id,
                    exc_info=True,
                )
                result.failed_predictions += 1
                continue
            result.predictions.append((lap.driver_id, lap_number, prediction))
            scores[lap.driver_id] = float(prediction["undercut_score"])

        result.alerts.extend(
            await alert_service.find_undercut_threats_at_lap(
                db, target.session_id, lap_number, scores
            )
        )
        logger.info(
            "%s lap %d/%d: %d prediction(s)",
            target.race_name,
            lap_number,
            target.end_lap,
            len(scores),
        )

    await redis_client.delete(gaps_key)
    result.seconds = time.monotonic() - started
    return result


async def write_session(db: AsyncSession, result: SessionPrecompute) -> None:
    """Replace the session's predictions and replay rows with result, in one transaction.

    ALL of the session's strategy_predictions are deleted, not only the
    window's: for a curated race they only ever came from replay runs, and
    older rows (other laps, older models, duplicates) would otherwise linger
    in its history.

    Args:
        db: Async DB session, with no transaction in progress.
        result: Output of compute_session.
    Returns:
        None.
    """
    session_id = result.target.session_id
    predicted_at = datetime.now(UTC)
    for model in (
        StrategyPrediction,
        ReplayLapTiming,
        ReplayGapSnapshot,
        ReplayAlertEvent,
        ReplayCarNumber,
    ):
        await db.execute(delete(model).where(model.session_id == session_id))

    rows: list[Any] = [
        StrategyPrediction(
            id=uuid.uuid4(),
            session_id=session_id,
            driver_id=driver_id,
            predicted_at=predicted_at,
            lap_number=lap_number,
            **prediction,
        )
        for driver_id, lap_number, prediction in result.predictions
    ]
    rows += [
        ReplayLapTiming(
            id=uuid.uuid4(),
            session_id=session_id,
            driver_id=timing.driver_id,
            lap_number=timing.lap_number,
            lap_start_seconds=timing.lap_start_seconds,
            lap_end_seconds=timing.lap_end_seconds,
        )
        for timing in result.lap_timings
    ]
    rows += [
        ReplayGapSnapshot(id=uuid.uuid4(), session_id=session_id, lap_number=lap, gaps=payload)
        for lap, payload in result.gap_snapshots.items()
    ]
    rows += [
        ReplayAlertEvent(
            id=uuid.uuid4(),
            session_id=session_id,
            lap_number=alert.lap_number,
            alert_type=alert.alert_type,
            driver_id=alert.driver_id,
            rival_driver_id=alert.rival_driver_id,
            message=alert.message,
            score=alert.score,
        )
        for alert in result.alerts
    ]
    rows += [
        ReplayCarNumber(
            id=uuid.uuid4(),
            session_id=session_id,
            driver_id=car.driver_id,
            car_number=car.car_number,
        )
        for car in result.car_numbers
    ]
    db.add_all(rows)
    await db.commit()


def _production_model_dates() -> dict[str, str]:
    """Upload date of each S3 production model, for the run summary."""
    settings = get_aws_settings()
    client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    try:
        objects = client.list_objects_v2(Bucket=settings.aws_bucket_name, Prefix="production/").get(
            "Contents", []
        )
    except (BotoCoreError, ClientError):
        logger.warning("Could not list S3 production models", exc_info=True)
        return {}
    return {
        obj["Key"].removeprefix("production/"): obj["LastModified"].strftime("%Y-%m-%d %H:%M UTC")
        for obj in objects
        if obj["Key"].endswith(".pkl")
    }


def _summary_line(result: SessionPrecompute) -> str:
    target = result.target
    return (
        f"{target.race_name} (laps {target.start_lap}-{target.end_lap}): "
        f"{len(result.predictions)} prediction(s), {result.failed_predictions} failed, "
        f"{len(result.alerts)} alert(s), {len(result.lap_timings)} lap timing(s), "
        f"{len(result.gap_snapshots)} gap snapshot(s), {len(result.car_numbers)} car number(s), "
        f"{result.seconds:.0f}s"
    )


async def run(session_id: uuid.UUID | None, dry_run: bool) -> list[SessionPrecompute]:
    """Precompute every requested curated session; write each unless dry_run.

    Args:
        session_id: One curated session, or None for all of them.
        dry_run: Compute and report, but write nothing to the database.
    Returns:
        The computed sessions.
    Raises:
        RuntimeError: a Demo Replay is running against this Redis — it writes
            the same gaps keys.
    """
    redis_client: aioredis.Redis = aioredis.from_url(  # type: ignore[type-arg]
        get_redis_settings().redis_url, decode_responses=True
    )
    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    results: list[SessionPrecompute] = []
    try:
        if await redis_client.get(DEMO_REPLAY_STATE_KEY) is not None:
            raise RuntimeError("A Demo Replay is running; stop it before precomputing")

        for name, uploaded in sorted(_production_model_dates().items()):
            logger.info("Model %s uploaded %s", name, uploaded)

        async with session_factory() as db:
            targets = await resolve_targets(db, session_id)
            code_to_driver_id = await _driver_code_map(db)
        if not targets:
            logger.warning("No curated races in this database; nothing to precompute")

        for target in targets:
            fastf1_session = _load_fastf1_session(target.season, target.round_number, "R")
            async with session_factory() as db:
                result = await compute_session(
                    db, redis_client, target, fastf1_session, code_to_driver_id
                )
                await db.rollback()
            if dry_run:
                logger.info("DRY RUN, not written: %s", _summary_line(result))
            else:
                async with session_factory() as db:
                    await write_session(db, result)
                logger.info("Written: %s", _summary_line(result))
            results.append(result)
    finally:
        try:
            await redis_client.aclose()  # type: ignore[attr-defined]
        finally:
            await engine.dispose()
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Precompute Demo Replay predictions, alerts, gaps, timings and car numbers."
    )
    parser.add_argument(
        "--session-id",
        type=uuid.UUID,
        default=None,
        help="Only this curated session (default: every curated race in the database)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute and report without writing")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args()
    try:
        results = asyncio.run(run(args.session_id, args.dry_run))
    except (ValueError, RuntimeError) as exc:
        logger.error("%s", exc)
        sys.exit(1)
    for result in results:
        print(("DRY RUN " if args.dry_run else "") + _summary_line(result), flush=True)


if __name__ == "__main__":
    main()
