"""Celery Beat task: auto-detect a starting Race session and launch ingestion.

Polls Ergast's race schedule every 5 minutes (see celery_app.py's
beat_schedule) looking for a Race (session_type "R") whose scheduled start
has arrived. On a match, launches ingest_live_session.py as a **detached
subprocess** rather than calling run_live_ingestor() inline — the worker
runs a single --pool=solo process handling telemetry_queue/prediction_queue/
alert_queue, and the ingestor blocks for up to 3 hours while itself
dispatching process_lap/run_strategy_prediction tasks back onto that same
worker. Running it inline would block the only worker thread for the whole
race, and its own .delay() calls would never execute — a self-deadlock.
Launching as a separate OS process avoids this entirely and matches how the
script already runs today (`make ingest-live`), just auto-launched instead
of manual.

See CLAUDE.md's Auto Race Detection section for the enable/disable switch,
the dedup key, and the grace window.
"""

import json
import logging
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import redis

from backend.core.config import get_live_timing_settings, get_redis_settings
from backend.scripts._ingest_common import combine_ergast_date_time
from backend.services.demo_service import DEMO_REPLAY_STATE_KEY
from backend.workers.celery_app import app

logger = logging.getLogger(__name__)

# How long after a Race's scheduled start we still consider it "just
# started" and worth auto-launching for. Wide on purpose — the Redis dedup
# key below already prevents a duplicate launch, so a generous window only
# helps cover a beat/worker outage near the green flag, never causes harm.
_GRACE_WINDOW = timedelta(minutes=30)

# Dedup key TTL: covers run_live_ingestor's 3h default max_duration plus a
# buffer, so a re-poll during the same race never launches a second
# ingestor. Not a CLAUDE.md-documented cache/prediction key (no data is
# stored under it) — see CLAUDE.md's Redis Cache Key Schema for the entry.
_TRIGGER_KEY_TTL_SECONDS = 4 * 60 * 60


def _trigger_key(season: int, round_number: int) -> str:
    return f"f1:{season}:{round_number}:R:auto_ingestion_triggered"


def _find_race_ready_for_ingestion(season: int) -> tuple[int, datetime] | None:
    """Find a Race session whose scheduled start is within the grace window.

    Args:
        season: Season year to check against Ergast's race schedule.
    Returns:
        (round_number, session_start_utc) for the first matching Race, or
        None if nothing has started recently.
    """
    from fastf1.ergast import Ergast

    schedule = Ergast().get_race_schedule(season)
    now = datetime.now(UTC)

    for _, race in schedule.iterrows():
        if "raceDate" not in race or "raceTime" not in race:
            continue
        start = combine_ergast_date_time(race["raceDate"], race["raceTime"])
        if start is not None and start <= now <= start + _GRACE_WINDOW:
            return int(race["round"]), start

    return None


def _already_triggered(client: redis.Redis, season: int, round_number: int) -> bool:  # type: ignore[type-arg]
    """Atomically claim the trigger key for this race; True if someone already has."""
    key = _trigger_key(season, round_number)
    claimed = client.set(key, "1", nx=True, ex=_TRIGGER_KEY_TTL_SECONDS)
    return not claimed


def _force_stop_demo_replay(client: redis.Redis) -> None:  # type: ignore[type-arg]
    """Terminate any running Demo Replay before launching real live ingestion.

    A Demo Replay and a live ingestor both write f1:{season}:{round}:gaps and
    f1:{season}:{round}:car:{n}:position — a real race always takes priority.
    SIGTERM routes into replay_pipeline.py's graceful shutdown handler. A
    bare NX-claim sentinel (a start still in progress) has no pid to signal;
    clearing the key is enough. See CLAUDE.md's Auto Race Detection section.

    Args:
        client: The task's sync Redis client (decode_responses=True).
    Returns:
        None.
    """
    raw = client.get(DEMO_REPLAY_STATE_KEY)
    if raw is None:
        return

    try:
        pid = json.loads(raw).get("pid")
    except (json.JSONDecodeError, AttributeError):
        pid = None

    if isinstance(pid, int):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        logger.warning("Force-stopped demo replay (pid %s) to launch live ingestion", pid)

    client.delete(DEMO_REPLAY_STATE_KEY)


def _launch_ingestion_subprocess(season: int, round_number: int) -> None:
    subprocess.Popen(  # noqa: S603 — fixed argv, no shell, no external input
        [
            sys.executable,
            "-m",
            "backend.scripts.ingest_live_session",
            "--season",
            str(season),
            "--round",
            str(round_number),
            "--session-type",
            "R",
        ],
        start_new_session=True,
    )


@app.task(name="check_for_live_session")  # type: ignore[untyped-decorator]
def check_for_live_session() -> None:
    """Beat-scheduled task: detect a starting Race session and auto-launch ingestion.

    Edge cases handled:
        - Feature disabled (auto_race_detection_enabled=False): no-op.
        - No Race session starting within the grace window: no-op.
        - Already triggered for this race (Redis dedup key present): no-op.
        - Ergast API unreachable: logged, task returns without raising.
    Returns:
        None.
    """
    if not get_live_timing_settings().auto_race_detection_enabled:
        logger.debug("Auto race detection disabled, skipping")
        return

    season = datetime.now(UTC).year
    try:
        found = _find_race_ready_for_ingestion(season)
    except Exception:
        logger.exception("Failed to fetch Ergast race schedule for season %d", season)
        return

    if found is None:
        logger.debug("No Race session starting within the grace window")
        return

    round_number, start = found
    client = redis.Redis.from_url(get_redis_settings().redis_url, decode_responses=True)
    try:
        if _already_triggered(client, season, round_number):
            logger.debug(
                "Season %d round %d already auto-triggered, skipping", season, round_number
            )
            return
        # The dedup claim is now held — a real race is launching. Stop any
        # active demo replay first so it can't keep writing the shared
        # timing/position keys the live ingestor is about to own.
        _force_stop_demo_replay(client)
    finally:
        client.close()

    logger.info(
        "Auto-launching live ingestor: season %d round %d (R), started %s",
        season,
        round_number,
        start,
    )
    _launch_ingestion_subprocess(season, round_number)
