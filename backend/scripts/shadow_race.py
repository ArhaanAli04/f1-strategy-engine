"""Shadow race (V3): run a whole recorded race through the REAL live pipeline.

Unit tests mock everything between the ingestor and the alerts. This tool runs the
real chain once, end to end, against the running Docker stack:

    F1's archived messages -> the real F1SignalRIngestor (real handlers, real Redis)
      -> real Celery (process_lap / run_strategy_prediction) -> the real worker
      -> Postgres (lap_data, strategy_predictions) -> alert_service -> alerts

under a THROWAWAY race (season 2098) so nothing real is touched, then checks the
result. It proves the wiring across process boundaries; it cannot prove anything
about F1's live socket. See docs/live-race-ingestion-and-strategy-gaps-monza-2026.md
section 7c (V3).

    python -m backend.scripts.shadow_race run --until-lap 12            # smoke run
    python -m backend.scripts.shadow_race run --speed 2                 # full race at 2x
    python -m backend.scripts.shadow_race verify --manifest <path>
    python -m backend.scripts.shadow_race cleanup --manifest <path>

The Docker worker must be running. `run` refuses to start if a real live race is being
ingested. Cleanup deletes only races of season 2098 whose event name starts with
"SHADOW RACE" (deleting a race cascades to its sessions, lap data, predictions and
alerts), plus that race's Redis keys.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import statistics
import subprocess
import sys
import time
import uuid
from collections import Counter
from collections.abc import Coroutine
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypeVar
from unittest.mock import patch

import redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_redis_settings
from backend.core.database import get_engine
from backend.models.driver import Driver
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.strategy import StrategyPrediction
from backend.models.telemetry import LapData
from backend.models.user import Alert
from backend.scripts import ingest_live_session
from backend.scripts import verify_live_feed_archive as harness
from backend.scripts._raw_feed_recorder import RawFeedRecorder, load_recording_counts
from backend.services.live_race_detection import detect_live_race_sync

logger = logging.getLogger(__name__)

T = TypeVar("T")

SHADOW_SEASON = 2098
SHADOW_EVENT_PREFIX = "SHADOW RACE"
WORKER_CONTAINER = "docker-worker-1"
# The live Monza 2026 race the archive comes from, and its real session in the local DB —
# used only to borrow the circuit and to prove the real data is left untouched.
DEFAULT_SOURCE_SEASON, DEFAULT_SOURCE_ROUND = 2026, 13
DEFAULT_REAL_SESSION_ID = "3ddc84bd-f10e-4870-9e98-631d79695beb"
DEFAULT_MANIFEST_DIR = Path("recordings") / "shadow"

# Streams fed beyond the four the replay harness uses: the lap count is what sets
# sessions.total_laps, and the weather feeds the tyre model's live weather key.
_EXTRA_TOPICS = {"LapCount": "lap_count", "WeatherData": "weather_data"}
_TOPIC_ORDER = {
    "DriverList": 0,
    "TrackStatus": 1,
    "LapCount": 2,
    "WeatherData": 3,
    "TimingAppData": 4,
    "TimingData": 5,
}
_QUEUES = ("telemetry_queue", "prediction_queue", "alert_queue")
_ALERT_MESSAGE = re.compile(r"Undercut threat: (\w+) on (\w+) \((\d+)%\)")
_PROGRESS_EVERY_SECONDS = 60.0
_QUEUE_SAMPLE_EVERY_SECONDS = 30.0
_MIN_POSITION_MATCH = 0.99
_MIN_PREDICTION_COVERAGE = 0.99
_MIN_LIVE_SHARE = 0.9
# An alert computed just before a car is flagged out may legitimately be committed just
# after it (worker lag), so only an alert this long after the flag counts as a violation.
_STOPPED_CAR_GRACE_SECONDS = 5.0


# --- pure helpers ---


def pace_delay(previous_ts: float, ts: float, speed: float, max_gap: float) -> float:
    """Seconds to wait before feeding a message, given the previous one.

    Args:
        previous_ts, ts: Stream times of the two messages, in seconds.
        speed: Replay speed factor (2.0 = twice as fast as the real race).
        max_gap: Cap on the stream-time gap honoured, so long dead air (the pre-race
            period, a red flag) does not take real time.
    Returns:
        A non-negative delay in wall-clock seconds.
    """
    return max(0.0, min(ts - previous_ts, max_gap)) / speed


def max_completed_lap(content: dict[str, Any]) -> int:
    """Highest NumberOfLaps carried by one TimingData message (0 if none)."""
    laps = [
        entry["NumberOfLaps"]
        for entry in content.get("Lines", {}).values()
        if isinstance(entry, dict) and isinstance(entry.get("NumberOfLaps"), int)
    ]
    return max(laps, default=0)


def parse_alert_message(message: str) -> tuple[str, str, int] | None:
    """'Undercut threat: VER on RUS (55%)' -> ('VER', 'RUS', 55); None if not that shape."""
    match = _ALERT_MESSAGE.match(message)
    return (match.group(1), match.group(2), int(match.group(3))) if match else None


def is_shadow_race(season: int, event_name: str | None) -> bool:
    """The ONLY races cleanup may delete: season 2098 with a SHADOW RACE event name."""
    return season == SHADOW_SEASON and (event_name or "").startswith(SHADOW_EVENT_PREFIX)


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile (0 < fraction <= 1) of a non-empty list."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * len(ordered)) - 1))]


# --- checks: pure functions from collected data to a verdict ---


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # PASS | FAIL | INFO | SKIP
    detail: str


@dataclass(frozen=True)
class AlertRecord:
    """One alert plus the trailing driver's latest stored lap state at that moment."""

    t: float
    trailing: str
    ahead: str
    trailing_lap: int | None
    trailing_tyre_age: int | None


def check_total_laps(actual: int | None, expected: int) -> Check:
    ok = actual == expected
    return Check(
        "sessions.total_laps set from F1's lap count",
        "PASS" if ok else "FAIL",
        f"stored {actual}, F1's own TotalLaps {expected}",
    )


def check_no_ghosts(ghost_cars: list[str], stale_lapped: list[str], handler_errors: int) -> Check:
    ok = not ghost_cars and not stale_lapped and handler_errors == 0
    return Check(
        "No out-of-race car in the published live standings",
        "PASS" if ok else "FAIL",
        f"ghost cars {ghost_cars or 'none'}, stale lapped gaps {stale_lapped or 'none'}, "
        f"swallowed handler errors {handler_errors}",
    )


def check_laps_persisted(
    expected: dict[str, int | None], actual: dict[str, int | None]
) -> list[Check]:
    """Every dispatched lap reached Postgres via Celery, and its position is F1's."""
    missing = [key for key in expected if key not in actual]
    comparable = [k for k, want in expected.items() if want is not None and k in actual]
    matched = sum(actual[k] == expected[k] for k in comparable)
    share = matched / len(comparable) if comparable else 0.0
    return [
        Check(
            "Every lap completion reached lap_data through Celery",
            "PASS" if expected and not missing else "FAIL",
            f"{len(expected) - len(missing)} of {len(expected)} persisted"
            + (f"; missing e.g. {missing[:5]}" if missing else ""),
        ),
        Check(
            f"Persisted lap_data.position matches F1's position (>= {_MIN_POSITION_MATCH:.0%})",
            "PASS" if comparable and share >= _MIN_POSITION_MATCH else "FAIL",
            f"{matched} of {len(comparable)} ({share:.1%})",
        ),
    ]


def check_predictions(
    dispatched_at: dict[str, float], predicted_at: dict[str, float]
) -> list[Check]:
    covered = [k for k in dispatched_at if k in predicted_at]
    coverage = len(covered) / len(dispatched_at) if dispatched_at else 0.0
    checks = [
        Check(
            f"A prediction exists for every dispatched lap (>= {_MIN_PREDICTION_COVERAGE:.0%})",
            "PASS" if dispatched_at and coverage >= _MIN_PREDICTION_COVERAGE else "FAIL",
            f"{len(covered)} of {len(dispatched_at)} ({coverage:.1%})",
        )
    ]
    lags = [predicted_at[k] - dispatched_at[k] for k in covered]
    if lags:
        checks.append(
            Check(
                "Prediction lag behind the lap dispatch (seconds)",
                "INFO",
                f"median {statistics.median(lags):.1f}, p95 {percentile(lags, 0.95):.1f}, "
                f"max {max(lags):.1f}",
            )
        )
    return checks


def check_pipeline_stats(stats: dict[str, int]) -> Check:
    """Live gaps, neighbours and alert pairing were actually used (loose, reported)."""

    def live_share(live: str, other: str) -> tuple[int, int, float]:
        a, b = stats.get(live, 0), stats.get(other, 0)
        return a, b, (a / (a + b) if a + b else 0.0)

    gap = live_share("gap_source_live", "gap_source_summed")
    neighbours = live_share("neighbors_source_live", "neighbors_source_db")
    pairing = live_share("alert_order_source_live", "alert_order_source_db")
    ok = gap[0] > 0 and neighbours[0] > 0 and pairing[0] > 0 and pairing[2] >= _MIN_LIVE_SHARE
    return Check(
        "Live standings drove the gaps, neighbours and alert pairing",
        "PASS" if ok else "FAIL",
        f"gap live/summed {gap[0]}/{gap[1]}; neighbours live/db {neighbours[0]}/{neighbours[1]}; "
        f"alert pairing live/db {pairing[0]}/{pairing[1]} ({pairing[2]:.0%})",
    )


def check_alerts(
    alerts: list[AlertRecord],
    out_wall: dict[str, float],
    total_laps: int | None,
) -> list[Check]:
    """No alert for a stopped car, fresh tyres, or too few laps left."""
    stopped, tyre, late = [], [], []
    for alert in alerts:
        for code in (alert.trailing, alert.ahead):
            flagged = out_wall.get(code)
            if flagged is not None and alert.t > flagged + _STOPPED_CAR_GRACE_SECONDS:
                stopped.append(f"{alert.trailing} on {alert.ahead} ({code} out)")
        if alert.trailing_tyre_age is not None and alert.trailing_tyre_age < 4:
            tyre.append(f"{alert.trailing} on {alert.ahead} (tyres {alert.trailing_tyre_age})")
        if (
            total_laps is not None
            and alert.trailing_lap is not None
            and total_laps - alert.trailing_lap < 15
        ):
            late.append(f"{alert.trailing} on {alert.ahead} (lap {alert.trailing_lap})")
    ok = not (stopped or tyre or late)
    return [
        Check(
            "No alert involves a stopped car, tyres <= 3 laps old, or < 15 laps left",
            "PASS" if ok else "FAIL",
            f"{len(alerts)} alert(s); stopped-car {stopped[:3] or 0}, "
            f"fresh-tyre {tyre[:3] or 0}, late-race {late[:3] or 0}",
        )
    ]


def check_ingest_stats(stats: dict[str, Any] | None) -> Check:
    if not stats:
        return Check("Ingestor stats were published", "FAIL", "no ingest_stats key found")
    first = stats.get("position_first_message_seq")
    ok = first is not None and stats.get("rankings_by_f1_position", 0) > 0
    return Check(
        "Ingest stats: F1 Position streamed and drove the ranking",
        "PASS" if ok else "FAIL",
        f"position_first_message_seq {first}, by F1 position "
        f"{stats.get('rankings_by_f1_position', 0)}, by gaps {stats.get('rankings_by_gaps', 0)}, "
        f"flagged out {stats.get('cars_flagged_out', 0)}, laps {stats.get('laps_dispatched', 0)}",
    )


def check_recording(fed: dict[str, int], recorded: dict[str, int] | None) -> Check:
    if recorded is None:
        return Check("Raw-feed recording matches what was fed", "FAIL", "no recording file found")
    wrong = {t: (fed[t], recorded.get(t, 0)) for t in fed if recorded.get(t, 0) != fed[t]}
    return Check(
        "Raw-feed recording matches what was fed, topic for topic",
        "PASS" if not wrong else "FAIL",
        f"fed {dict(fed)}" + (f"; differing (fed, recorded): {wrong}" if wrong else ""),
    )


def check_real_data_untouched(before: dict[str, Any], after: dict[str, Any]) -> Check:
    changed = {
        k: (before[k], after[k])
        for k in ("lap_data", "predictions", "alerts")
        if before[k] != after[k]
    }
    new_keys = sorted(set(after["redis_keys"]) - set(before["redis_keys"]))
    ok = not changed and not new_keys
    return Check(
        "The real Monza data and Redis keys are untouched",
        "PASS" if ok else "FAIL",
        f"row counts {'unchanged' if not changed else changed}, new real keys {new_keys or 'none'}",
    )


# Only ERROR/CRITICAL lines count. A bare Traceback is not enough: the periodic Ergast
# schedule check logs a failed request at WARNING with a traceback while it falls back to
# its cache, whereas a genuine task failure is always logged at ERROR before its traceback.
_LOG_PROBLEM = re.compile(r"\b(ERROR|CRITICAL)\b")


def check_worker_logs(log_text: str | None) -> Check:
    if log_text is None:
        return Check(
            "No errors in the worker log during the run", "SKIP", "docker logs unavailable"
        )
    bad = [line.strip()[:160] for line in log_text.splitlines() if _LOG_PROBLEM.search(line)]
    return Check(
        "No errors in the worker log during the run",
        "PASS" if not bad else "FAIL",
        "none" if not bad else f"{len(bad)} line(s), e.g. {bad[:3]}",
    )


def format_checks(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = [f"{c.status:<5} {c.name:<{width}}  {c.detail}" for c in checks]
    failed = sum(c.status == "FAIL" for c in checks)
    lines.append("")
    lines.append(
        f"{len(checks)} checks: {failed} failed" if failed else f"{len(checks)} checks: none failed"
    )
    return "\n".join(lines)


# --- manifest: what a run recorded, for verify/cleanup ---


@dataclass
class Manifest:
    run_id: str
    season: int
    round_number: int
    race_id: str
    session_id: str
    source_season: int
    source_round: int
    real_session_id: str
    speed: float
    max_gap: float
    until_lap: int | None
    started_at: float = 0.0
    ended_at: float = 0.0
    expected_total_laps: int = 0
    fed_counts: dict[str, int] = field(default_factory=dict)
    dispatched: dict[str, dict[str, float | int | None]] = field(default_factory=dict)
    out_wall: dict[str, float] = field(default_factory=dict)
    ghost_cars: list[str] = field(default_factory=list)
    stale_lapped: list[str] = field(default_factory=list)
    handler_errors: int = 0
    queue_samples: list[list[float]] = field(default_factory=list)
    recording_path: str | None = None
    real_before: dict[str, Any] = field(default_factory=dict)
    final_max_lap: int = 0
    cleaned: bool = False

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path) -> Manifest:
        return cls(**json.loads(path.read_text()))


# --- database / redis helpers ---


async def _with_fresh_pool(coro: Coroutine[Any, Any, T]) -> T:
    """Run one async phase, then dispose the shared engine's pool.

    The engine is a process-wide singleton, and each asyncio.run() gets a NEW event loop.
    A connection opened in one loop cannot be reused in the next (asyncpg on Windows fails
    with "Event loop is closed"), so every phase must leave the pool empty.
    """
    try:
        return await coro
    finally:
        await get_engine().dispose()


def _run_async(coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(_with_fresh_pool(coro))


def _redis_client() -> redis.Redis:  # type: ignore[type-arg]
    return redis.Redis.from_url(get_redis_settings().redis_url, decode_responses=True)


def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def snapshot_real_data(
    real_session_id: uuid.UUID,
    season: int,
    round_number: int,
    client: redis.Redis,  # type: ignore[type-arg]
) -> dict[str, Any]:
    """Row counts and Redis keys for the REAL race, so a run can prove it left them alone."""
    async with _session_factory()() as db:
        counts: dict[str, Any] = {}
        for name, model in (
            ("lap_data", LapData),
            ("predictions", StrategyPrediction),
            ("alerts", Alert),
        ):
            counts[name] = (
                await db.execute(
                    select(func.count())
                    .select_from(model)
                    .where(model.session_id == real_session_id)
                )
            ).scalar_one()
    counts["redis_keys"] = sorted(client.scan_iter(f"f1:{season}:{round_number}:*"))
    return counts


@dataclass
class ShadowContext:
    race_id: uuid.UUID
    session_id: uuid.UUID
    round_number: int
    driver_code_to_id: dict[str, uuid.UUID]
    code_by_id: dict[str, str]


async def create_shadow_session(real_session_id: uuid.UUID) -> ShadowContext:
    """Create the throwaway race + R session, borrowing the real race's circuit."""
    async with _session_factory()() as db:
        circuit_id, race_date = (
            await db.execute(
                select(Race.circuit_id, Race.race_date)
                .join(SessionModel, SessionModel.race_id == Race.id)
                .where(SessionModel.id == real_session_id)
            )
        ).one()
        highest = (
            await db.execute(
                select(func.max(Race.round_number)).where(Race.season == SHADOW_SEASON)
            )
        ).scalar_one()
        round_number = (highest or 0) + 1
        race = Race(
            id=uuid.uuid4(),
            season=SHADOW_SEASON,
            round_number=round_number,
            circuit_id=circuit_id,
            race_date=race_date,
            status="scheduled",
            event_name=f"{SHADOW_EVENT_PREFIX} {round_number} (V3 replay of the Monza feed)",
        )
        session_row = SessionModel(
            id=uuid.uuid4(), race_id=race.id, session_type="R", session_date=race_date
        )
        db.add_all([race, session_row])
        await db.commit()
        drivers = (await db.execute(select(Driver.code, Driver.id))).all()
    return ShadowContext(
        race.id,
        session_row.id,
        round_number,
        {row.code: row.id for row in drivers},
        {str(driver_id): code for code, driver_id in drivers},
    )


# --- run ---


def _feed_messages(
    archive: harness.Archive, connect_at: float
) -> list[tuple[float, str, dict[str, Any]]]:
    messages = [
        (ts, topic, content)
        for topic, entries in archive.items()
        for ts, content in entries
        if ts > connect_at
    ]
    messages.sort(key=lambda m: (m[0], _TOPIC_ORDER.get(m[1], 9)))
    return messages


def run_shadow(args: argparse.Namespace) -> int:
    client = _redis_client()
    status = detect_live_race_sync(client)
    if status.is_live:
        logger.error("Refusing to run: a real live race appears to be ingested (%s)", status.reason)
        return 1

    real_session_id = uuid.UUID(args.real_session_id)
    logger.warning(
        "Fetching the archived feed for %s R%s ...", args.source_season, args.source_round
    )
    archive = harness.fetch_archive(args.source_season, args.source_round, "R", _EXTRA_TOPICS)
    connect_at = harness.detect_connect_at(archive)
    total_laps = max(
        (
            c.get("TotalLaps", 0)
            for _, c in archive.get("LapCount", [])
            if isinstance(c.get("TotalLaps"), int)
        ),
        default=0,
    )

    real_before = _run_async(
        snapshot_real_data(real_session_id, args.source_season, args.source_round, client)
    )
    ctx = _run_async(create_shadow_session(real_session_id))
    season, round_number = SHADOW_SEASON, ctx.round_number
    manifest = Manifest(
        run_id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        season=season,
        round_number=round_number,
        race_id=str(ctx.race_id),
        session_id=str(ctx.session_id),
        source_season=args.source_season,
        source_round=args.source_round,
        real_session_id=str(real_session_id),
        speed=args.speed,
        max_gap=args.max_gap,
        until_lap=args.until_lap,
        expected_total_laps=total_laps,
        real_before=real_before,
    )
    manifest_path = (
        Path(args.manifest)
        if args.manifest
        else (DEFAULT_MANIFEST_DIR / f"shadow_{manifest.run_id}.json")
    )
    logger.warning(
        "Shadow race created: season %d round %d session %s (manifest %s)",
        season,
        round_number,
        ctx.session_id,
        manifest_path,
    )
    manifest.save(manifest_path)

    recorder = RawFeedRecorder.try_create(Path("recordings"), season, round_number, "R")
    manifest.recording_path = str(recorder.path) if recorder is not None else None
    ingestor = ingest_live_session.F1SignalRIngestor(
        season=season,
        round_number=round_number,
        session_id=ctx.session_id,
        car_number_to_driver_id={},
        driver_code_to_id=ctx.driver_code_to_id,
        redis_client=client,
        no_auth=True,
        recorder=recorder,
    )

    truth = harness.TruthTracker()
    car_by_code: dict[str, str] = {}
    report = harness.ReplayReport()
    original_process_lap = ingest_live_session.process_lap.delay

    def tracked_process_lap(raw_lap: dict[str, Any]) -> None:
        code = ctx.code_by_id.get(str(raw_lap["driver_id"]), "?")
        car = car_by_code.get(code)
        car_truth = truth.cars.get(car) if car is not None else None
        expected = car_truth.position if car_truth is not None and car_truth.active else None
        manifest.dispatched[f"{code}:{int(raw_lap['lap_number'])}"] = {
            "t": time.time(),
            "expected_position": expected,
        }
        original_process_lap(raw_lap)

    error_counter = harness._ErrorCounter()
    ingestor_logger = logging.getLogger(ingest_live_session.__name__)
    ingestor_logger.addHandler(error_counter)
    started = time.time()
    manifest.started_at = started
    try:
        with patch.object(ingest_live_session.process_lap, "delay", tracked_process_lap):
            _drive(
                ingestor,
                archive,
                connect_at,
                args,
                manifest,
                truth,
                car_by_code,
                report,
                client,
                ctx,
            )
    except KeyboardInterrupt:
        logger.warning("Interrupted — saving the manifest for what ran so far")
    finally:
        manifest.ended_at = time.time()
        ingestor.publish_stats(force=True)
        if recorder is not None:
            recorder.close()
        manifest.ghost_cars = sorted(
            set(report.ghost_retired_samples) | set(report.ghost_hidden_samples)
        )
        manifest.stale_lapped = sorted(report.stale_lapped_samples)
        manifest.handler_errors = error_counter.count
        ingestor_logger.removeHandler(error_counter)
        manifest.save(manifest_path)
    logger.warning(
        "Feed finished: %d laps dispatched in %.0f min. Let the worker drain, then run:\n"
        "  python -m backend.scripts.shadow_race verify --manifest %s",
        len(manifest.dispatched),
        (manifest.ended_at - started) / 60,
        manifest_path,
    )
    return 0


def _drive(
    ingestor: ingest_live_session.F1SignalRIngestor,
    archive: harness.Archive,
    connect_at: float,
    args: argparse.Namespace,
    manifest: Manifest,
    truth: harness.TruthTracker,
    car_by_code: dict[str, str],
    report: harness.ReplayReport,
    client: redis.Redis,  # type: ignore[type-arg]
    ctx: ShadowContext,
) -> None:
    """Snapshot through _on_subscribe_result, then every later message through _on_feed, paced."""
    driver_list = harness._snapshot(archive, "DriverList", connect_at)
    for car, entry in driver_list.items():
        if isinstance(entry, dict) and entry.get("Tla"):
            car_by_code[str(entry["Tla"])] = car
    timing_snapshot = harness._snapshot(archive, "TimingData", connect_at)
    truth.apply(timing_snapshot.get("Lines", {}), connect_at)
    snapshot_topics = {
        topic: harness._snapshot(archive, topic, connect_at)
        for topic in (
            "DriverList",
            "TimingAppData",
            "TimingData",
            "TrackStatus",
            "LapCount",
            "WeatherData",
        )
    }
    ingestor._on_open()
    ingestor._on_subscribe_result(SimpleNamespace(result=snapshot_topics))

    messages = _feed_messages(archive, connect_at)
    gaps_key = f"f1:{manifest.season}:{manifest.round_number}:gaps"
    fed: Counter[str] = Counter()
    schedule_start = time.monotonic()
    virtual = 0.0
    previous_ts = messages[0][0] if messages else 0.0
    max_lap = 0
    last_progress = last_sample = schedule_start

    for ts, topic, content in messages:
        # No laps and no worker load until the first lap completes, so the long pre-race
        # section is fast-forwarded; racing then runs at the requested speed.
        speed = args.speed if max_lap > 0 else args.prerace_speed
        virtual += pace_delay(previous_ts, ts, speed, args.max_gap)
        previous_ts = ts
        wait = virtual - (time.monotonic() - schedule_start)
        if wait > 0.02:
            time.sleep(wait)

        if topic == "TimingData":
            truth.apply(content.get("Lines", {}), ts)
            for car, car_truth in truth.cars.items():
                if car_truth.first_out_flag_s is not None:
                    code = next((c for c, n in car_by_code.items() if n == car), car)
                    manifest.out_wall.setdefault(code, time.time())
            max_lap = max(max_lap, max_completed_lap(content))
        ingestor._on_feed([topic, content])
        fed[topic] += 1

        if topic == "TimingData":
            raw = client.get(gaps_key)
            if raw:
                entries = [
                    {**e, "driver_id": ctx.code_by_id.get(str(e["driver_id"]), str(e["driver_id"]))}
                    for e in json.loads(raw)["gaps"]
                ]
                harness.audit_tower(
                    report, entries, truth.cars, car_by_code, ingestor._car_live_gap_state, ts
                )

        now = time.monotonic()
        if now - last_sample >= _QUEUE_SAMPLE_EVERY_SECONDS:
            last_sample = now
            depths = _queue_depths(client)
            manifest.queue_samples.append([time.time(), *(float(v) for v in depths.values())])
        if now - last_progress >= _PROGRESS_EVERY_SECONDS:
            last_progress = now
            logger.warning(
                "progress: stream %.0f s, leader lap %d, %d laps dispatched, queues %s",
                ts,
                max_lap,
                len(manifest.dispatched),
                _queue_depths(client),
            )
        if args.until_lap is not None and max_lap >= args.until_lap:
            logger.warning("Reached lap %d — stopping the feed", max_lap)
            break

    manifest.fed_counts = dict(fed)
    manifest.final_max_lap = max_lap


# --- verify ---


async def _fetch_run_data(manifest: Manifest) -> dict[str, Any]:
    session_id = uuid.UUID(manifest.session_id)
    async with _session_factory()() as db:
        codes = {str(i): c for i, c in (await db.execute(select(Driver.id, Driver.code))).all()}
        total_laps = (
            await db.execute(select(SessionModel.total_laps).where(SessionModel.id == session_id))
        ).scalar_one_or_none()
        laps = (
            await db.execute(
                select(
                    LapData.driver_id,
                    LapData.lap_number,
                    LapData.position,
                    LapData.tyre_age_laps,
                    LapData.created_at,
                ).where(LapData.session_id == session_id)
            )
        ).all()
        predictions = (
            await db.execute(
                select(
                    StrategyPrediction.driver_id,
                    StrategyPrediction.lap_number,
                    StrategyPrediction.predicted_at,
                ).where(StrategyPrediction.session_id == session_id)
            )
        ).all()
        alerts = (
            await db.execute(
                select(Alert.triggered_at, Alert.message).where(Alert.session_id == session_id)
            )
        ).all()
    return {
        "codes": codes,
        "total_laps": total_laps,
        "laps": laps,
        "predictions": predictions,
        "alerts": alerts,
    }


def _queue_depths(client: redis.Redis) -> dict[str, int]:  # type: ignore[type-arg]
    """Waiting tasks per queue, plus tasks the worker has prefetched but not finished.

    A plain queue length misses the latter: once a worker prefetches a message it leaves
    the Redis list and sits in kombu's `unacked` hash until the task completes, so a busy
    worker can look idle.
    """
    depths = {q: int(client.llen(q)) for q in _QUEUES}
    depths["unacked"] = int(client.hlen("unacked"))
    return depths


def _wait_for_queues_to_drain(client: redis.Redis, timeout: float) -> bool:  # type: ignore[type-arg]
    deadline = time.monotonic() + timeout
    quiet = 0
    while time.monotonic() < deadline:
        depth = sum(_queue_depths(client).values())
        quiet = quiet + 1 if depth == 0 else 0
        if quiet >= 3:
            return True
        logger.warning("waiting for the worker to drain: %d queued task(s)", depth)
        time.sleep(10)
    return False


def _worker_log_since(started_at: float) -> str | None:
    since = datetime.fromtimestamp(started_at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["docker", "logs", "--since", since, WORKER_CONTAINER],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return proc.stdout + proc.stderr


def run_verify(args: argparse.Namespace) -> int:
    manifest = Manifest.load(Path(args.manifest))
    client = _redis_client()
    drained = _wait_for_queues_to_drain(client, args.drain_timeout)
    data = _run_async(_fetch_run_data(manifest))
    codes = data["codes"]

    lap_rows = {f"{codes[str(r.driver_id)]}:{r.lap_number}": r for r in data["laps"]}
    persisted_positions = {k: r.position for k, r in lap_rows.items()}
    expected_positions: dict[str, int | None] = {}
    for key, record in manifest.dispatched.items():
        wanted = record["expected_position"]
        expected_positions[key] = int(wanted) if wanted is not None else None
    dispatched_at = {k: float(v["t"] or 0.0) for k, v in manifest.dispatched.items()}
    predicted_at = {
        f"{codes[str(p.driver_id)]}:{p.lap_number}": p.predicted_at.timestamp()
        for p in data["predictions"]
        if p.lap_number is not None
    }

    lap_state: dict[str, list[tuple[float, int, int]]] = {}
    for key, row in lap_rows.items():
        lap_state.setdefault(key.split(":")[0], []).append(
            (row.created_at.timestamp(), row.lap_number, row.tyre_age_laps)
        )
    for series in lap_state.values():
        series.sort()

    alerts: list[AlertRecord] = []
    for row in data["alerts"]:
        parsed = parse_alert_message(row.message)
        if parsed is None:
            continue
        trailing, ahead, _ = parsed
        t = row.triggered_at.timestamp()
        latest = [s for s in lap_state.get(trailing, []) if s[0] <= t]
        alerts.append(
            AlertRecord(
                t,
                trailing,
                ahead,
                latest[-1][1] if latest else None,
                latest[-1][2] if latest else None,
            )
        )

    key_prefix = f"f1:{manifest.season}:{manifest.round_number}"
    raw_stats = client.get(f"{key_prefix}:ingest_stats")
    ingest_stats = json.loads(raw_stats) if raw_stats else None
    pipeline = {k: int(v) for k, v in client.hgetall(f"{key_prefix}:pipeline_stats").items()}
    recorded = (
        load_recording_counts(Path(manifest.recording_path))
        if manifest.recording_path and Path(manifest.recording_path).exists()
        else None
    )
    real_after = _run_async(
        snapshot_real_data(
            uuid.UUID(manifest.real_session_id),
            manifest.source_season,
            manifest.source_round,
            client,
        )
    )

    checks: list[Check] = [
        Check(
            "Worker queues drained after the feed",
            "PASS" if drained else "FAIL",
            "empty"
            if drained
            else f"still busy after {args.drain_timeout:.0f}s — results below are partial",
        ),
        check_total_laps(data["total_laps"], manifest.expected_total_laps),
        check_no_ghosts(manifest.ghost_cars, manifest.stale_lapped, manifest.handler_errors),
        *check_laps_persisted(expected_positions, persisted_positions),
        *check_predictions(dispatched_at, predicted_at),
        check_pipeline_stats(pipeline),
        *check_alerts(
            alerts, manifest.out_wall, data["total_laps"] or manifest.expected_total_laps or None
        ),
        check_ingest_stats(ingest_stats),
        check_recording({**manifest.fed_counts, "Subscribe": 1}, recorded),
        check_real_data_untouched(manifest.real_before, real_after),
        check_worker_logs(_worker_log_since(manifest.started_at)),
    ]
    if manifest.queue_samples:
        peak = max(sample[2] for sample in manifest.queue_samples)
        checks.append(
            Check("Peak prediction-queue depth during the feed", "INFO", f"{peak:.0f} task(s)")
        )
    print(format_checks(checks))
    return 1 if any(c.status == "FAIL" for c in checks) else 0


# --- cleanup ---


async def _delete_shadow_races(only_race_id: uuid.UUID | None) -> list[tuple[str, int]]:
    """Delete shadow races (never anything else). Returns [(session_id, round)] removed."""
    removed: list[tuple[str, int]] = []
    async with _session_factory()() as db:
        query = select(Race.id, Race.season, Race.round_number, Race.event_name)
        if only_race_id is not None:
            query = query.where(Race.id == only_race_id)
        for race_id, season, round_number, event_name in (await db.execute(query)).all():
            if not is_shadow_race(season, event_name):
                continue
            session_ids = (
                (await db.execute(select(SessionModel.id).where(SessionModel.race_id == race_id)))
                .scalars()
                .all()
            )
            removed.extend((str(s), round_number) for s in session_ids)
            await db.execute(delete(Race).where(Race.id == race_id))
        await db.commit()
    return removed


def run_cleanup(args: argparse.Namespace) -> int:
    client = _redis_client()
    manifest = Manifest.load(Path(args.manifest)) if args.manifest else None
    removed = _run_async(
        _delete_shadow_races(uuid.UUID(manifest.race_id) if manifest is not None else None)
    )
    for session_id, round_number in removed:
        patterns = [f"f1:{SHADOW_SEASON}:{round_number}:*", f"f1:alerts:dedup:{session_id}:*"]
        deleted = sum(
            client.delete(key) for pattern in patterns for key in client.scan_iter(pattern)
        )
        print(
            f"removed shadow round {round_number} (session {session_id}) and {deleted} Redis key(s)"
        )
    if not removed:
        print("nothing to remove (no matching shadow race)")
    if manifest is not None and args.manifest:
        manifest.cleaned = True
        manifest.save(Path(args.manifest))
    return 0


# --- CLI ---


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="feed the archived race through the real pipeline")
    run.add_argument("--source-season", type=int, default=DEFAULT_SOURCE_SEASON)
    run.add_argument("--source-round", type=int, default=DEFAULT_SOURCE_ROUND)
    run.add_argument("--real-session-id", default=DEFAULT_REAL_SESSION_ID)
    run.add_argument("--speed", type=float, default=1.0, help="2.0 = twice real time (default 1)")
    run.add_argument("--max-gap", type=float, default=5.0, help="cap on dead air honoured, seconds")
    run.add_argument(
        "--prerace-speed",
        type=float,
        default=60.0,
        help="speed until the first lap completes (default 60)",
    )
    run.add_argument(
        "--until-lap", type=int, default=None, help="stop once the leader completes this lap"
    )
    run.add_argument("--manifest", default=None)

    verify = commands.add_parser("verify", help="check the outcome of a run")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--drain-timeout", type=float, default=1800.0)

    cleanup = commands.add_parser("cleanup", help="delete a run's throwaway race and Redis keys")
    cleanup.add_argument("--manifest", default=None, help="omit to remove every shadow race")

    args = parser.parse_args()
    return {"run": run_shadow, "verify": run_verify, "cleanup": run_cleanup}[args.command](args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s: %(message)s")
    sys.exit(main())
