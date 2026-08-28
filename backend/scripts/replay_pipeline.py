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

Day 43 (Demo Replay) additions — all published directly to Redis by this
script, not via Celery, since none of the three need ML inference or DB
persistence, just to land in the same keys the live ingestor writes to:

- Car-number resolution: ingest_live_session.py's live DriverList topic is
  the only thing that ever populates f1:{season}:{round}:driver:{id}:
  car_number — a replay has no such topic, so without this, Circuit Map
  Panel's useDriverCarNumbers would stay permanently empty and no dot could
  ever be colored/matched. Resolved once at startup via FastF1 (see
  _ingest_common.resolve_car_numbers) and published before the first lap.
- Targeted gap-computation fix: telemetry_service._compute_session_gaps's
  SUM(lap_time_seconds) reconstruction is confirmed broken by NULL-lap gaps
  (see CLAUDE.md's Deferred Wiring) — rather than fixing that general,
  4-call-site codepath, this publishes directly to the same
  f1:{season}:{round}:gaps key ingest_live_session.py's _publish_live_gaps
  writes to (get_session_gaps' @cacheable reads it first, so no other code
  changes anywhere are needed), computed from FastF1's own authoritative
  per-lap Time/Position columns instead of our DB's summed deltas. Scoped
  deliberately to just the 3 curated Demo Replay sessions' lap ranges — see
  CLAUDE.md's Day 43 entry for why a general fix was not attempted today.
- Position playback: reads Day 43's driver_positions table (see
  ingest_position_data.py) and republishes it to f1:{season}:{round}:car:
  {car_number}:position at a real 1Hz cadence, matching the live
  Position.z-authenticated path's own key/shape exactly — CircuitMapPanel
  needs no replay-specific frontend code. Runs as a single continuous
  background thread synchronized on ABSOLUTE session time across the whole
  curated window, not per-lap — an earlier per-lap-burst version grouped
  purely by driver_positions.timestamp_in_lap (relative to each driver's OWN
  lap start), which silently collapsed real gaps: two drivers 30s apart in
  a real race both show "0 seconds into lap 43" at their own pace, so
  grouping by that value alone put the whole field at the same relative
  spot on track regardless of actual time gaps — confirmed live against
  British GP 2026 Round 9 lap 43 (LEC to HAD really 53.2s apart via FastF1's
  own LapStartTime, but both appeared together in the old version). The
  per-lap-burst structure also had a rate/TTL mismatch: position updates
  only happened during each lap's own ~90-160s burst, then nothing for the
  rest of that lap's --rate-paced driver-dispatch phase, so the 3s-TTL
  position keys visibly expired and dots vanished until the next lap's
  burst. Running one continuous absolute-time-synchronized stream for the
  whole window (see _build_position_timeline/_run_position_timeline) fixes
  both: real gaps are preserved (a trailing driver simply shows their
  older, further-back position, not the leader's relative spot), and
  publishing never stops for the whole window's duration, independent of
  --rate.

Run via:
    python -m backend.scripts.replay_pipeline --session-id <uuid> --rate fast
    python -m backend.scripts.replay_pipeline --session-id <uuid> \
        --start-lap 43 --end-lap 52   # restrict to a curated lap window
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from types import FrameType
from typing import Any

import fastf1
import redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_ml_settings, get_redis_settings
from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.telemetry import DriverPosition, LapData
from backend.scripts._ingest_common import resolve_car_numbers
from backend.services.live_race_detection import detect_live_race_sync
from backend.workers.prediction_worker import run_strategy_prediction
from backend.workers.telemetry_worker import process_lap

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_RATE_PRESETS = {"fast": 5, "normal": 30, "slow": 90}
_DEFAULT_RATE_LABEL = "fast"

# Live ingestion refreshes this key every few seconds straight from real
# TimingData messages (_GAPS_KEY_TTL_SECONDS=30 in ingest_live_session.py) —
# replay only refreshes it once per LAP (position playback alone can take
# ~90-100 real seconds/lap at true 1Hz pace), so a matching short TTL would
# expire mid-lap and fall through to the broken DB reconstruction this
# feature exists to avoid. Generous on purpose; refreshed every lap anyway.
_REPLAY_GAPS_KEY_TTL_SECONDS = 600
# Matches ingest_live_session.py's _POSITION_KEY_TTL_SECONDS — replay
# publishes at the same real ~1Hz cadence live Position.z updates at.
_REPLAY_POSITION_KEY_TTL_SECONDS = 3
# Written once at startup and never refreshed mid-replay (no live DriverList
# topic to re-trigger it) — generous flat TTL rather than trying to predict
# total replay duration up front.
_REPLAY_CAR_NUMBER_KEY_TTL_SECONDS = 4 * 60 * 60
# Real wall-clock pacing for position playback, independent of --rate
# (which paces lap-completion/prediction dispatch) — "1Hz within each lap"
# per CLAUDE.md's Day 43 Planned Feature spec.
_POSITION_SAMPLE_INTERVAL_SECONDS = 1.0


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
    parser.add_argument(
        "--start-lap",
        type=int,
        default=None,
        help="First lap number to replay (inclusive). Default: from the start of the session.",
    )
    parser.add_argument(
        "--end-lap",
        type=int,
        default=None,
        help="Last lap number to replay (inclusive). Default: through the end of the session.",
    )
    args = parser.parse_args()

    if args.start_lap is not None and args.end_lap is not None and args.end_lap < args.start_lap:
        parser.error("--end-lap must be >= --start-lap")

    return args


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


async def _resolve_replay_context(session_id: uuid.UUID) -> tuple[int, int, str]:
    """Resolve a session's (season, round_number, session_type) for the FastF1 lookups below.

    Args:
        session_id: Session to replay.
    Returns:
        (season, round_number, session_type).
    """
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    query = (
        select(Race.season, Race.round_number, SessionModel.session_type)
        .join(SessionModel, SessionModel.race_id == Race.id)
        .where(SessionModel.id == session_id)
    )
    async with session_factory() as db:
        row = (await db.execute(query)).one()
    await engine.dispose()
    return int(row[0]), int(row[1]), str(row[2])


def _load_fastf1_session(season: int, round_number: int, session_type: str) -> fastf1.core.Session:
    """Load a FastF1 session's laps + driver info — car numbers and authoritative
    per-lap Time/Position for _resolve_car_numbers/_compute_lap_gaps below.

    Args:
        season, round_number, session_type: Identify the session.
    Returns:
        The loaded FastF1 session.
    """
    settings = get_ml_settings()
    # FastF1's enable_cache requires the directory to already exist — it does
    # NOT create it. ingest_historical.py / ingest_live_session.py both
    # makedirs here; without it a fresh container (no prior FastF1 run —
    # e.g. the backend container launching this as a subprocess) crashes with
    # NotADirectoryError before the replay publishes anything.
    os.makedirs(settings.fastf1_cache_dir, exist_ok=True)
    fastf1.Cache.enable_cache(settings.fastf1_cache_dir)
    fastf1_session = fastf1.get_session(season, round_number, session_type)
    logger.info(
        "Loading FastF1 data for %d round %d %s — the first replay of a given "
        "session downloads it from FastF1 (~30-60s); every run after that is "
        "served from the local cache",
        season,
        round_number,
        session_type,
    )
    fastf1_session.load(laps=True, telemetry=False, weather=False, messages=False)
    return fastf1_session


def _publish_car_numbers(
    redis_client: redis.Redis,  # type: ignore[type-arg]
    season: int,
    round_number: int,
    car_number_to_driver_id: dict[str, uuid.UUID],
) -> None:
    """Write f1:{season}:{round}:driver:{id}:car_number for every resolved car.

    Same key ingest_live_session.py's DriverList handler writes — see module
    docstring for why replay needs to do this itself.
    """
    for car_number, driver_id in car_number_to_driver_id.items():
        redis_client.setex(
            f"f1:{season}:{round_number}:driver:{driver_id}:car_number",
            _REPLAY_CAR_NUMBER_KEY_TTL_SECONDS,
            car_number,
        )
    logger.info("Published %d car-number mapping(s) for replay", len(car_number_to_driver_id))


def _compute_lap_gaps(
    fastf1_session: fastf1.core.Session,
    lap_number: int,
    code_to_driver_id: dict[str, uuid.UUID],
    session_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Authoritative gap snapshot for one lap, from FastF1's own Time/Position columns.

    Deliberately NOT telemetry_service._compute_session_gaps' SUM(lap_time_
    seconds) reconstruction — see module docstring. Time is FastF1's absolute
    session-elapsed timestamp when each driver crossed the line on this lap,
    so a plain subtraction between position-adjacent drivers is always valid
    (unlike a summed delta, it was never built by adding up individually
    nullable per-lap deltas). laps_behind is always 0: within a single lap
    number's rows, cross-lap-boundary comparison (the reason
    _compute_session_gaps needs it) never arises.

    Args:
        fastf1_session: Loaded FastF1 session (see _load_fastf1_session).
        lap_number: Lap to compute a snapshot for.
        code_to_driver_id: Driver.code -> Driver.id.
        session_id: Session being replayed, for the payload's session_id field.
    Returns:
        SessionGapsResponse-shaped dict, or None if no driver has a valid
        Time/Position for this lap number (nothing to publish).
    """
    laps = fastf1_session.laps
    lap_rows = laps[laps["LapNumber"] == lap_number].dropna(subset=["Time", "Position"])
    if lap_rows.empty:
        return None

    entries = []
    for row in lap_rows.sort_values("Position").itertuples():
        driver_id = code_to_driver_id.get(row.Driver)
        if driver_id is None:
            continue
        entries.append(
            {
                "driver_id": str(driver_id),
                "position": int(row.Position),
                "time_seconds": row.Time.total_seconds(),
            }
        )
    if not entries:
        return None

    gaps = []
    for i, entry in enumerate(entries):
        gap_ahead = 0.0 if i == 0 else entry["time_seconds"] - entries[i - 1]["time_seconds"]
        gap_behind = (
            0.0 if i == len(entries) - 1 else entries[i + 1]["time_seconds"] - entry["time_seconds"]
        )
        gaps.append(
            {
                "driver_id": entry["driver_id"],
                "lap_number": lap_number,
                "position": entry["position"],
                "gap_to_ahead_seconds": gap_ahead,
                "gap_to_behind_seconds": gap_behind,
                "laps_behind": 0,
            }
        )
    # "source": "replay" — live_race_detection.detect_live_race treats a gaps
    # key as a live race ONLY when its payload says "source": "live"
    # (ingest_live_session.py). Marking this explicitly keeps a replay's own
    # gaps key (which lingers up to its 600s TTL after the replay ends) from
    # ever being mistaken for one. Ignored by SessionGapsResponse (extra
    # fields), so no consumer needs to change.
    return {"session_id": str(session_id), "gaps": gaps, "source": "replay"}


def _publish_gaps(
    redis_client: redis.Redis,  # type: ignore[type-arg]
    season: int,
    round_number: int,
    payload: dict[str, Any],
) -> None:
    """Write a gaps snapshot to the same key get_session_gaps' @cacheable reads first."""
    redis_client.setex(
        f"f1:{season}:{round_number}:gaps",
        _REPLAY_GAPS_KEY_TTL_SECONDS,
        json.dumps(payload),
    )


async def _fetch_all_positions(
    session_id: uuid.UUID, start_lap: int | None = None, end_lap: int | None = None
) -> list[dict[str, Any]]:
    """Persisted driver_positions rows for a session, optionally restricted to a lap range.

    Args:
        session_id: Session being replayed.
        start_lap, end_lap: Same --start-lap/--end-lap restriction replay()
            applies to the lap-completion dispatch loop — without this,
            position playback would always run for the FULL ingested window
            regardless of a narrower requested range (confirmed during Day
            43 verification: a --start-lap/--end-lap-restricted 2-lap test
            run still took the full ~1242s of the entire 10-lap curated
            window to finish, since position playback ignored the range).
    Returns:
        One dict per row: driver_id, lap_number, timestamp_in_lap, x, y.
        Empty list if ingest_position_data.py was never run for this session,
        or no ingested position data falls within [start_lap, end_lap] —
        either way, not an error, just nothing to play back.
    """
    engine = get_engine()
    session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    query = select(
        DriverPosition.driver_id,
        DriverPosition.lap_number,
        DriverPosition.timestamp_in_lap,
        DriverPosition.x,
        DriverPosition.y,
    ).where(DriverPosition.session_id == session_id)
    if start_lap is not None:
        query = query.where(DriverPosition.lap_number >= start_lap)
    if end_lap is not None:
        query = query.where(DriverPosition.lap_number <= end_lap)
    async with session_factory() as db:
        rows = (await db.execute(query)).all()
    await engine.dispose()
    return [
        {
            "driver_id": str(row.driver_id),
            "lap_number": row.lap_number,
            "timestamp_in_lap": row.timestamp_in_lap,
            "x": row.x,
            "y": row.y,
        }
        for row in rows
    ]


# (absolute_session_time_seconds, x, y), sorted ascending by time, per driver_id.
PositionTimeline = dict[str, list[tuple[float, float, float]]]


# (absolute_time_seconds, lap_number), ascending — real-time boundaries at
# which the earliest-starting driver begins each lap, used to trigger gaps
# recomputation off the SAME clock position playback runs on (see
# _run_position_timeline's docstring for why this must not be the --rate-
# paced dispatch loop).
LapBoundaries = list[tuple[float, int]]


def _build_position_timeline(
    fastf1_session: fastf1.core.Session,
    code_to_driver_id: dict[str, uuid.UUID],
    position_rows: list[dict[str, Any]],
) -> tuple[PositionTimeline, float, float, LapBoundaries] | None:
    """Convert lap-relative driver_positions rows into one shared absolute-time timeline.

    driver_positions.timestamp_in_lap is relative to each driver's OWN lap
    start — grouping directly on that value (the original implementation)
    silently erases real gaps, since two drivers running similar pace both
    reach "5 seconds into lap 43" at the same relative point regardless of
    how many real seconds apart they actually are (see module docstring).
    FastF1's own per-lap LapStartTime (already loaded on fastf1_session) is
    the session-absolute anchor needed to undo that: absolute_time =
    lap_start_time.total_seconds() + timestamp_in_lap.

    Args:
        fastf1_session: Loaded FastF1 session (laps=True — see _load_fastf1_session).
        code_to_driver_id: Driver.code -> Driver.id.
        position_rows: All rows from _fetch_all_positions.
    Returns:
        (timeline, t_min, t_max, lap_boundaries) — per-driver sorted
        (time, x, y) lists, the overall time range to iterate, and the
        real-time lap-transition boundaries within [t_min, t_max] — or None
        if position_rows is empty or no row could be matched to a real
        LapStartTime.
    """
    if not position_rows:
        return None

    driver_id_to_code = {v: k for k, v in code_to_driver_id.items()}
    lap_start_seconds: dict[tuple[str, int], float] = {}
    for lap in fastf1_session.laps.itertuples():
        lap_start_time = lap.LapStartTime
        if lap_start_time != lap_start_time:  # NaT/NaN never equals itself — cheap isna() check
            continue
        lap_start_seconds[(lap.Driver, int(lap.LapNumber))] = lap_start_time.total_seconds()

    timeline: PositionTimeline = {}
    for row in position_rows:
        code = driver_id_to_code.get(uuid.UUID(row["driver_id"]))
        if code is None:
            continue
        start_seconds = lap_start_seconds.get((code, row["lap_number"]))
        if start_seconds is None:
            continue
        absolute_time = start_seconds + row["timestamp_in_lap"]
        timeline.setdefault(row["driver_id"], []).append((absolute_time, row["x"], row["y"]))

    if not timeline:
        return None

    all_times: list[float] = []
    for samples in timeline.values():
        samples.sort(key=lambda s: s[0])
        all_times.append(samples[0][0])
        all_times.append(samples[-1][0])
    t_min, t_max = min(all_times), max(all_times)

    # Earliest starter's LapStartTime per lap_number — the moment the race
    # "enters" that lap in real time, same convention a real Timing Tower's
    # lap counter (tied to the leader) would use.
    earliest_start_per_lap: dict[int, float] = {}
    for (_, lap_number), start_seconds in lap_start_seconds.items():
        if (
            lap_number not in earliest_start_per_lap
            or start_seconds < earliest_start_per_lap[lap_number]
        ):
            earliest_start_per_lap[lap_number] = start_seconds
    lap_boundaries: LapBoundaries = sorted(
        (start_seconds, lap_number)
        for lap_number, start_seconds in earliest_start_per_lap.items()
        if t_min <= start_seconds <= t_max
    )

    return timeline, t_min, t_max, lap_boundaries


def _run_position_timeline(
    redis_client: redis.Redis,  # type: ignore[type-arg]
    stop_event: threading.Event,
    season: int,
    round_number: int,
    session_id: uuid.UUID,
    driver_id_to_car_number: dict[str, str],
    timeline: PositionTimeline,
    t_min: float,
    t_max: float,
    lap_boundaries: LapBoundaries,
    fastf1_session: fastf1.core.Session,
    code_to_driver_id: dict[str, uuid.UUID],
) -> None:
    """Background-thread target: stream the whole window's positions at a real 1Hz cadence.

    Advances one shared clock from t_min to t_max in real ~1s steps —
    independent of --rate, which paces the main thread's per-driver
    process_lap/run_strategy_prediction dispatch instead (see module
    docstring). At each tick, every driver publishes their OWN latest
    sample whose absolute time has already passed — a trailing driver who
    hasn't reached "now" in their own timeline yet simply keeps showing
    their older, further-back-on-track position, correctly preserving real
    gaps instead of snapping everyone to the same relative lap position.
    A driver with no sample yet at all (e.g. they start the very first lap
    of the curated window later than its earliest starter, so they have no
    prior-lap data in the window to fall back on) is skipped entirely —
    matches how the frontend already treats a driver simply absent from the
    positions list, degrading gracefully rather than showing a wrong value.

    Also recomputes and republishes gaps whenever a lap_boundaries entry is
    crossed — moved here from the main dispatch loop (Day 43 verification
    finding): the dispatch loop's pace is rate_seconds x drivers_in_lap,
    which only coincidentally tracks real lap duration. Confirmed via real
    FastF1 data for British GP 2026 Round 9: at --rate fast (5s), the
    dispatch clock ran +39s AHEAD of real time through laps 43-47 (normal
    ~93s laps vs. its own fixed ~100s/lap pace), then swung to -249s BEHIND
    by lap 52 once real lap times ballooned to 143-173s under bunched/lapped
    traffic — a ~4-minute gap between what the Timing Tower showed and what
    the Circuit Map dots were actually depicting at the same moment. Driving
    both off this same real-time clock keeps them locked together.

    All of one tick's SETEX calls go out in a single Redis pipeline (see
    Day 43 verification notes on why — one round trip per driver made real
    per-tick pacing drift under the intended 1.0s cadence).

    Args:
        redis_client: Sync Redis client.
        stop_event: Set to stop early (KeyboardInterrupt in the main thread).
        season, round_number: For the f1:{season}:{round}:car:{car_number}:position key.
        session_id: For _compute_lap_gaps' payload.
        driver_id_to_car_number: A driver with no resolved car number is
            skipped (can't build the key).
        timeline: Output of _build_position_timeline.
        t_min, t_max: Overall absolute-time range to iterate.
        lap_boundaries: Output of _build_position_timeline.
        fastf1_session, code_to_driver_id: Forwarded to _compute_lap_gaps.
    Returns:
        None.
    """
    pointers: dict[str, int] = dict.fromkeys(timeline, -1)
    next_boundary_index = 0
    tick = t_min
    while tick <= t_max and not stop_event.is_set():
        publish_started = time.monotonic()

        while (
            next_boundary_index < len(lap_boundaries)
            and lap_boundaries[next_boundary_index][0] <= tick
        ):
            _, lap_number = lap_boundaries[next_boundary_index]
            next_boundary_index += 1
            try:
                gaps_payload = _compute_lap_gaps(
                    fastf1_session, lap_number, code_to_driver_id, session_id
                )
                if gaps_payload is not None:
                    _publish_gaps(redis_client, season, round_number, gaps_payload)
            except Exception:  # noqa: BLE001 — sync extras are supplementary, never fatal
                logger.exception("Failed publishing gaps for lap %d", lap_number)

        pipeline = redis_client.pipeline(transaction=False)
        for driver_id, samples in timeline.items():
            pointer = pointers[driver_id]
            while pointer + 1 < len(samples) and samples[pointer + 1][0] <= tick:
                pointer += 1
            pointers[driver_id] = pointer
            if pointer < 0:
                continue  # This driver's own window data hasn't started yet.

            car_number = driver_id_to_car_number.get(driver_id)
            if car_number is None:
                continue
            _, x, y = samples[pointer]
            key = f"f1:{season}:{round_number}:car:{car_number}:position"
            payload = {"x": x, "y": y, "z": None, "timestamp": f"replay+{tick:.0f}s"}
            pipeline.setex(key, _REPLAY_POSITION_KEY_TTL_SECONDS, json.dumps(payload))
        pipeline.execute()

        tick += _POSITION_SAMPLE_INTERVAL_SECONDS
        if tick <= t_max:
            elapsed = time.monotonic() - publish_started
            time.sleep(max(0.0, _POSITION_SAMPLE_INTERVAL_SECONDS - elapsed))


def _reraise_sigterm_as_interrupt(signum: int, frame: FrameType | None) -> None:
    """Route SIGTERM into replay()'s existing graceful KeyboardInterrupt path.

    /demo/replay/stop and race_detection_worker.py's kill-switch both stop
    this process with SIGTERM. Without this handler SIGTERM's default action
    kills the process outright, skipping the finally block that stops the
    position thread and terminates the alert_worker subprocess.
    """
    raise KeyboardInterrupt


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
    start_lap: int | None = None,
    end_lap: int | None = None,
) -> None:
    """Replay one session's persisted laps through process_lap + run_strategy_prediction.

    Also publishes Day 43's Demo Replay sync data directly to Redis — car
    numbers once at startup, then per-lap gaps + position playback whenever
    the loop below advances to a new lap_number (see module docstring).

    Args:
        session_id: Session to replay.
        rate_seconds: Delay in seconds between successive lap-completion dispatches.
        start_alert_worker: Whether to spawn alert_worker.py as a subprocess.
        limit: Stop after dispatching this many lap events total (across all
            drivers), or None to replay the full session. total_laps (the race
            distance shown in each progress line) is computed from the full,
            unlimited fetch — a limit truncates which events are *dispatched*,
            not what "race distance" the progress line reports.
        start_lap, end_lap: Restrict replay to this inclusive lap range (e.g.
            a curated Demo Replay window) — None means from the start /
            through the end of the session, respectively.
    Returns:
        None. Ctrl+C stops early (the alert_worker subprocess, if started, is
        always terminated on exit).
    """
    signal.signal(signal.SIGTERM, _reraise_sigterm_as_interrupt)

    all_laps = asyncio.run(_fetch_laps(session_id))
    if not all_laps:
        logger.warning("No lap data for session %s — nothing to replay", session_id)
        return
    # Built from the FULL fetch, before any lap-range/--limit narrowing below —
    # car-number resolution should know about every driver in the session,
    # not just whichever ones happen to fall inside a curated window.
    code_to_driver_id = {lap["driver_code"]: uuid.UUID(lap["driver_id"]) for lap in all_laps}

    laps = all_laps
    if start_lap is not None:
        laps = [lap for lap in laps if lap["lap_number"] >= start_lap]
    if end_lap is not None:
        laps = [lap for lap in laps if lap["lap_number"] <= end_lap]
    if not laps:
        logger.warning(
            "No laps in range [%s, %s] for session %s — nothing to replay",
            start_lap,
            end_lap,
            session_id,
        )
        return
    if limit is not None:
        laps = laps[:limit]

    season, round_number, session_type = asyncio.run(_resolve_replay_context(session_id))
    fastf1_session = _load_fastf1_session(season, round_number, session_type)
    car_number_to_driver_id = resolve_car_numbers(fastf1_session, code_to_driver_id)
    driver_id_to_car_number = {
        str(driver_id): car_number for car_number, driver_id in car_number_to_driver_id.items()
    }

    redis_client: redis.Redis = redis.Redis.from_url(  # type: ignore[type-arg]
        get_redis_settings().redis_url, decode_responses=True
    )
    _publish_car_numbers(redis_client, season, round_number, car_number_to_driver_id)

    # Position playback runs as one continuous absolute-time-synchronized
    # background thread for the whole window, independent of --rate — see
    # module docstring for why a per-lap-burst approach doesn't work.
    all_position_rows = asyncio.run(_fetch_all_positions(session_id, start_lap, end_lap))
    timeline_result = _build_position_timeline(fastf1_session, code_to_driver_id, all_position_rows)
    position_thread: threading.Thread | None = None
    stop_event = threading.Event()
    # Whenever position data exists, gaps are recomputed from inside the
    # position thread's real-time clock instead of the main loop's --rate-
    # paced lap-transition detection below — see _run_position_timeline's
    # docstring for the confirmed drift this fixes. Sessions with no
    # ingested position data (anything other than the 3 curated Demo Replay
    # sessions) fall back to the old --rate-paced trigger so gaps still work
    # at all, just without the real-time-locked guarantee.
    if timeline_result is not None:
        timeline, t_min, t_max, lap_boundaries = timeline_result
        position_thread = threading.Thread(
            target=_run_position_timeline,
            args=(
                redis_client,
                stop_event,
                season,
                round_number,
                session_id,
                driver_id_to_car_number,
                timeline,
                t_min,
                t_max,
                lap_boundaries,
                fastf1_session,
                code_to_driver_id,
            ),
            daemon=True,
        )
        position_thread.start()
        logger.info(
            "Position playback started: %d driver(s), ~%.0fs of real session time",
            len(timeline),
            t_max - t_min,
        )
    else:
        logger.info(
            "No position data for this session — Circuit Map dots unavailable; "
            "gaps will be paced by --rate instead of real time"
        )

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
    current_lap_number: int | None = None
    try:
        for i, lap in enumerate(laps):
            if lap["lap_number"] != current_lap_number:
                current_lap_number = lap["lap_number"]
                if position_thread is None:  # fallback — see the block above that starts it
                    try:
                        gaps_payload = _compute_lap_gaps(
                            fastf1_session, current_lap_number, code_to_driver_id, session_id
                        )
                        if gaps_payload is not None:
                            _publish_gaps(redis_client, season, round_number, gaps_payload)
                    except Exception:  # noqa: BLE001 — sync extras are supplementary, never fatal
                        logger.exception("Failed publishing gaps for lap %d", current_lap_number)

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

        # Dispatch is done; a curated replay now spends most of its runtime
        # here, waiting out the real-time position playback. This join() is
        # INSIDE the try so a SIGTERM during it (via
        # _reraise_sigterm_as_interrupt — /demo/replay/stop, the kill-switch,
        # or Ctrl+C) is caught by the handler below and exits cleanly instead
        # of escaping as a traceback.
        logger.info("Replay complete: %d lap events dispatched", dispatched)
        if position_thread is not None and position_thread.is_alive():
            logger.info("Waiting for position playback to finish...")
            position_thread.join()
    except KeyboardInterrupt:
        logger.info("Stopped after dispatching %d/%d lap events", dispatched, total_events)
        stop_event.set()
    finally:
        stop_event.set()
        if position_thread is not None:
            position_thread.join(timeout=15)
        if alert_worker_process is not None:
            logger.info("Stopping alert_worker.py subprocess...")
            alert_worker_process.terminate()
            alert_worker_process.wait(timeout=10)
        # Delete this replay's own gaps key so it can't linger (up to its
        # 600s TTL) and be misread as a live race by a later
        # /demo/replay/start. Runs on normal completion and on SIGTERM
        # (routed to KeyboardInterrupt); only a SIGKILL would skip it, and
        # the "source": "replay" payload marker covers that case.
        try:
            redis_client.delete(f"f1:{season}:{round_number}:gaps")
        except redis.RedisError:
            logger.warning("Could not delete replay gaps key on shutdown", exc_info=True)
        redis_client.close()


def _guard_against_live_race() -> None:
    """Abort the process if a real live race is currently being ingested.

    A replay and a live ingestor both write f1:{season}:{round}:gaps /
    :car:{n}:position — running them at once corrupts the shared keys. The
    /demo/replay/start endpoint performs the same check before launching this
    script as a subprocess; this is the direct-CLI-invocation backstop
    (Day 43 Part 3.2), with no bypass flag by design.
    """
    redis_client: redis.Redis = redis.Redis.from_url(  # type: ignore[type-arg]
        get_redis_settings().redis_url, decode_responses=True
    )
    try:
        status = detect_live_race_sync(redis_client)
    finally:
        redis_client.close()

    if status.is_live:
        logger.error(
            "Refusing to start replay: %s. Wait until the live session finishes.",
            status.reason,
        )
        sys.exit(1)


def main() -> None:
    args = _parse_args()
    _guard_against_live_race()
    try:
        replay(
            args.session_id,
            args.rate,
            start_alert_worker=not args.no_alert_worker,
            limit=args.limit,
            start_lap=args.start_lap,
            end_lap=args.end_lap,
        )
    except KeyboardInterrupt:
        # SIGTERM (via _reraise_sigterm_as_interrupt) or Ctrl+C arriving
        # during startup — before replay()'s own handler is in scope, e.g.
        # mid-FastF1-load. replay() catches it once its dispatch/join phase
        # is running; this covers the phase before that. Nothing durable is
        # published yet, so a clean exit is all that's needed.
        logger.info("Replay stopped before it began playing back")


if __name__ == "__main__":
    main()
