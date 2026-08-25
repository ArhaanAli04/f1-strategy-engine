"""Replay a completed session's persisted lap_data through the FULL live pipeline.

Companion to ingest_historical.py (which only writes lap_data/tire_stints, no
Celery involvement — see its own docstring) and tests/load/replay_publisher.py
(which only replays the WS broadcast layer, bypassing Celery entirely — see
its docstring). This script instead re-dispatches the same two Celery tasks
ingest_live_session.py's _handle_timing_data calls on every real lap
completion — process_lap (persist + WS publish) and run_strategy_prediction
(ML inference + StrategyPrediction persist + f1:predictions:{session_id}
publish) — against an already-ingested historical session, lap by lap, at a
configurable pace. Enables testing StrategyPrediction persistence, alert
dispatch, and WS live updates end-to-end without a live SignalR connection.

process_lap's insert is idempotent (ON CONFLICT DO NOTHING on
session_id/driver_id/lap_number — see telemetry_worker.py), so replaying
already-ingested rows is safe and does not duplicate them.

Requires the Celery worker already running and consuming telemetry_queue/
prediction_queue/alert_queue (docker compose up worker — see
infra/docker/Dockerfile.worker's -Q flag) — this script only enqueues tasks,
it does not execute them itself.

Run via:
    python -m backend.scripts.replay_pipeline --session-id <uuid> --rate fast
"""

import argparse
import asyncio
import logging
import subprocess
import sys
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.telemetry import LapData
from backend.workers.prediction_worker import run_strategy_prediction
from backend.workers.telemetry_worker import process_lap

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_RATE_PRESETS = {"fast": 5, "normal": 30, "slow": 90}
_DEFAULT_RATE_LABEL = "fast"


def _parse_rate(value: str) -> int:
    """Resolve --rate's value: a named preset, or a custom positive integer of seconds.

    Args:
        value: Raw --rate string from argparse.
    Returns:
        Seconds to sleep between successive lap-completion dispatches.
    Raises:
        argparse.ArgumentTypeError: value is neither a known preset nor a
            positive integer.
    """
    if value in _RATE_PRESETS:
        return _RATE_PRESETS[value]
    try:
        seconds = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--rate must be one of {list(_RATE_PRESETS)} or an integer number "
            f"of seconds, got {value!r}"
        ) from None
    if seconds <= 0:
        raise argparse.ArgumentTypeError("--rate must be a positive number of seconds")
    return seconds


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay a completed session's persisted laps through the full live pipeline."
    )
    parser.add_argument("--session-id", type=uuid.UUID, required=True, help="Session to replay")
    parser.add_argument(
        "--rate",
        type=_parse_rate,
        default=_RATE_PRESETS[_DEFAULT_RATE_LABEL],
        metavar="{fast,normal,slow,N}",
        help=(
            "Delay between lap-completion events: 'fast' (5s, default, good for "
            "testing), 'normal' (30s, realistic worker load), 'slow' (90s, real "
            "race pace), or a custom integer N (seconds)"
        ),
    )
    parser.add_argument(
        "--no-alert-worker",
        action="store_true",
        help="Don't spawn alert_worker.py as a subprocess (e.g. one is already running elsewhere)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "Stop after dispatching N lap events total, across all drivers "
            "(not N race laps — e.g. --limit 110 replays the first 5 laps for "
            "a 22-driver field). Default: no limit, replay the full session."
        ),
    )
    return parser.parse_args()


async def _fetch_laps(session_id: uuid.UUID) -> list[dict[str, Any]]:
    """Persisted lap_data rows for a session, ordered (lap_number, driver_id).

    Args:
        session_id: Session to replay.
    Returns:
        One dict per lap: raw_lap fields matching LapDataCreate/
        ingest_live_session.py's raw_lap shape, plus driver_code and
        total_laps (race distance, the max lap_number across the session) for
        progress printing.
    """
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    query = (
        select(LapData, Driver.code)
        .join(Driver, LapData.driver_id == Driver.id)
        .where(LapData.session_id == session_id)
        .order_by(LapData.lap_number, LapData.driver_id)
    )
    async with session_factory() as db:
        rows = (await db.execute(query)).all()
    await engine.dispose()

    total_laps = max((lap.lap_number for lap, _ in rows), default=0)
    return [
        {
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
            "driver_code": driver_code,
            "total_laps": total_laps,
        }
        for lap, driver_code in rows
    ]


def _start_alert_worker() -> subprocess.Popen[bytes]:
    """Spawn alert_worker.py's listen_for_predictions() as a standalone subprocess.

    stdout/stderr are inherited from this process, so alert_worker's own
    startup/error logging interleaves directly with replay progress — actual
    per-alert dispatch logging happens in the Celery worker process handling
    dispatch_alert (see `docker compose logs worker -f`), not here.

    Args:
        None.
    Returns:
        The subprocess handle.
    """
    logger.info("Starting alert_worker.py subprocess...")
    return subprocess.Popen([sys.executable, "-m", "backend.workers.alert_worker"])  # noqa: S603


def replay(
    session_id: uuid.UUID,
    rate_seconds: int,
    start_alert_worker: bool,
    limit: int | None = None,
) -> None:
    """Replay one session's persisted laps through process_lap + run_strategy_prediction.

    Args:
        session_id: Session to replay.
        rate_seconds: Delay in seconds between successive lap-completion dispatches.
        start_alert_worker: Whether to spawn alert_worker.py as a subprocess.
        limit: Stop after dispatching this many lap events total (across all
            drivers), or None to replay the full session. total_laps (the race
            distance shown in each progress line) is computed from the full,
            unlimited fetch — a limit truncates which events are *dispatched*,
            not what "race distance" the progress line reports.
    Returns:
        None. Ctrl+C stops early (the alert_worker subprocess, if started, is
        always terminated on exit).
    """
    laps = asyncio.run(_fetch_laps(session_id))
    if not laps:
        logger.warning("No lap data for session %s — nothing to replay", session_id)
        return
    if limit is not None:
        laps = laps[:limit]

    total_events = len(laps)
    estimated_minutes = total_events * rate_seconds / 60
    # flush=True: stdout is block-buffered (not line-buffered) whenever it's
    # not attached to a TTY (e.g. redirected to a file, or piped through a
    # log collector) — without this, progress never appears until the buffer
    # fills or the process exits, defeating the point of live progress output.
    print(f"Replaying {total_events} laps at {rate_seconds}s intervals", flush=True)
    print(f"Estimated completion: {estimated_minutes:.1f} minutes", flush=True)

    alert_worker_process = _start_alert_worker() if start_alert_worker else None

    dispatched = 0
    try:
        for i, lap in enumerate(laps):
            raw_lap = {k: v for k, v in lap.items() if k not in ("driver_code", "total_laps")}
            process_lap.delay(raw_lap)
            run_strategy_prediction.delay(raw_lap)
            dispatched += 1

            lap_time = (
                f"{lap['lap_time_seconds']:.3f}s" if lap["lap_time_seconds"] is not None else "N/A"
            )
            print(
                f"Lap {lap['lap_number']}/{lap['total_laps']} — "
                f"{lap['driver_code']} — {lap['compound']} — {lap_time}",
                flush=True,
            )

            if i < total_events - 1:
                time.sleep(rate_seconds)
    except KeyboardInterrupt:
        logger.info("Stopped early after dispatching %d/%d lap events", dispatched, total_events)
    else:
        logger.info("Replay complete: %d lap events dispatched", dispatched)
    finally:
        if alert_worker_process is not None:
            logger.info("Stopping alert_worker.py subprocess...")
            alert_worker_process.terminate()
            alert_worker_process.wait(timeout=10)


def main() -> None:
    args = _parse_args()
    replay(
        args.session_id,
        args.rate,
        start_alert_worker=not args.no_alert_worker,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
