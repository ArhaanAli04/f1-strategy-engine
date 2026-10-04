"""Play back a precomputed Demo Replay on a real-time clock, with no Celery worker.

The production counterpart of replay_pipeline.py (docs/internal/demo-deployment-
plan-2026.md, Day 4). replay_pipeline.py sends every lap through the worker,
which re-runs the ML models; this plays back what precompute_replay.py already
stored for the curated window, and publishes exactly what a replay publishes
today, to the same Redis keys and channels:

- car numbers, once, from replay_car_numbers;
- car positions every second, from driver_positions placed on the session clock
  by replay_lap_timings.lap_start_seconds;
- gaps (the timing tower), every second, ranked by how far round the lap each
  car is, worked out from its stored line crossings (replay_lap_timings), so
  the tower moves with the cars on the map instead of once per lap. See the
  "timing tower" section below. replay_gap_snapshots gives the starting order
  and which cars are still running;
- each driver's lap-completion event (the payload telemetry_worker.process_lap
  publishes) when that driver really finished the lap, from lap_data and
  replay_lap_timings.lap_end_seconds. At the first moment every driver's event
  for the lap before the window is sent too, so the race page switches to its
  replay view straight away instead of computing predictions itself;
- alerts, from replay_alert_events, when the trailing driver finishes that lap,
  written for every subscribed user by alert_service.dispatch_alert.

Predictions need nothing published: the race page reads the stored rows through
/strategy/{session_id}/{driver_id}/history and shows those up to the latest lap
event.

It must stay light enough for the web machine that launches it: no FastF1,
pandas or ML imports, so nothing from replay_pipeline.py.

Times are seconds on FastF1's session clock, the clock precompute_replay.py
stored; the timeline is expressed in seconds from the window's first moment.
"""

import argparse
import asyncio
import json
import logging
import math
import signal
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.core.config import get_redis_settings
from backend.core.database import get_engine
from backend.models.race import Race
from backend.models.race import Session as SessionModel
from backend.models.replay import (
    ReplayAlertEvent,
    ReplayCarNumber,
    ReplayGapSnapshot,
    ReplayLapTiming,
)
from backend.models.telemetry import DriverPosition, LapData
from backend.schemas.alert_schema import AlertType
from backend.scripts._replay_common import (
    CAR_NUMBER_KEY_TTL_SECONDS,
    GAPS_KEY_TTL_SECONDS,
    POSITION_KEY_TTL_SECONDS,
    car_number_key,
    gaps_key,
    guard_against_live_race,
    position_key,
    reraise_sigterm_as_interrupt,
)
from backend.services import alert_service

logger = logging.getLogger(__name__)

# Positions are published every second, matching live Position.z updates.
POSITION_TICK_SECONDS = 1.0

# --- The stored window -----------------------------------------------------


@dataclass(frozen=True)
class LapTimingRow:
    """When one driver started and finished one lap, on the session clock."""

    driver_id: str
    lap_number: int
    lap_start_seconds: float | None
    lap_end_seconds: float | None


@dataclass(frozen=True)
class PositionRow:
    """One stored position sample, relative to its driver's own lap start."""

    driver_id: str
    lap_number: int
    timestamp_in_lap: float
    x: float
    y: float


@dataclass(frozen=True)
class AlertRow:
    """One stored undercut alert (a replay_alert_events row)."""

    lap_number: int
    alert_type: str
    driver_id: str
    rival_driver_id: str | None
    message: str


@dataclass
class PlaybackData:
    """Everything playback needs for one curated window, loaded once."""

    session_id: uuid.UUID
    season: int
    round_number: int
    start_lap: int
    end_lap: int
    car_numbers: dict[str, str] = field(default_factory=dict)
    lap_timings: list[LapTimingRow] = field(default_factory=list)
    positions: list[PositionRow] = field(default_factory=list)
    gap_snapshots: dict[int, dict[str, Any]] = field(default_factory=dict)
    alerts: list[AlertRow] = field(default_factory=list)
    # The lap-completion payloads, for the lap before the window and every lap in it.
    lap_events: list[dict[str, Any]] = field(default_factory=list)


def lap_event_payload(lap: LapData) -> dict[str, Any]:
    """The lap-completion event telemetry_worker._publish_lap_completed publishes.

    Args:
        lap: A lap_data row.
    Returns:
        The same eight fields, JSON-serialisable.
    """
    return {
        "driver_id": str(lap.driver_id),
        "session_id": str(lap.session_id),
        "lap_number": lap.lap_number,
        "lap_time_seconds": lap.lap_time_seconds,
        "compound": lap.compound,
        "sector1_seconds": lap.sector1_seconds,
        "sector2_seconds": lap.sector2_seconds,
        "sector3_seconds": lap.sector3_seconds,
    }


async def load_playback_data(
    db: AsyncSession, session_id: uuid.UUID, start_lap: int, end_lap: int
) -> PlaybackData:
    """Read one curated window's stored replay data.

    Args:
        db: Async DB session.
        session_id: The curated race session.
        start_lap, end_lap: The inclusive curated window.
    Returns:
        The window's PlaybackData. Lap events and the gap snapshots include the
        lap before the window.
    Raises:
        ValueError: session_id is not a session in this database.
    """
    context = (
        await db.execute(
            select(Race.season, Race.round_number)
            .join(SessionModel, SessionModel.race_id == Race.id)
            .where(SessionModel.id == session_id)
        )
    ).one_or_none()
    if context is None:
        raise ValueError(f"No session {session_id} in this database")
    season, round_number = context

    data = PlaybackData(session_id, season, round_number, start_lap, end_lap)
    in_window = (ReplayLapTiming.lap_number >= start_lap, ReplayLapTiming.lap_number <= end_lap)

    car_rows = await db.execute(
        select(ReplayCarNumber.driver_id, ReplayCarNumber.car_number).where(
            ReplayCarNumber.session_id == session_id
        )
    )
    data.car_numbers = {str(driver_id): car_number for driver_id, car_number in car_rows.all()}

    timing_rows = await db.execute(
        select(ReplayLapTiming).where(ReplayLapTiming.session_id == session_id, *in_window)
    )
    data.lap_timings = [
        LapTimingRow(str(t.driver_id), t.lap_number, t.lap_start_seconds, t.lap_end_seconds)
        for t in timing_rows.scalars().all()
    ]

    position_rows = await db.execute(
        select(
            DriverPosition.driver_id,
            DriverPosition.lap_number,
            DriverPosition.timestamp_in_lap,
            DriverPosition.x,
            DriverPosition.y,
        ).where(
            DriverPosition.session_id == session_id,
            DriverPosition.lap_number >= start_lap,
            DriverPosition.lap_number <= end_lap,
        )
    )
    data.positions = [
        PositionRow(str(driver_id), lap_number, timestamp_in_lap, x, y)
        for driver_id, lap_number, timestamp_in_lap, x, y in position_rows.all()
    ]

    snapshot_rows = await db.execute(
        select(ReplayGapSnapshot.lap_number, ReplayGapSnapshot.gaps).where(
            ReplayGapSnapshot.session_id == session_id,
            ReplayGapSnapshot.lap_number >= start_lap - 1,
            ReplayGapSnapshot.lap_number <= end_lap,
        )
    )
    data.gap_snapshots = dict(snapshot_rows.tuples().all())

    alert_rows = await db.execute(
        select(ReplayAlertEvent).where(
            ReplayAlertEvent.session_id == session_id,
            ReplayAlertEvent.lap_number >= start_lap,
            ReplayAlertEvent.lap_number <= end_lap,
        )
    )
    data.alerts = [
        AlertRow(
            a.lap_number,
            a.alert_type,
            str(a.driver_id),
            str(a.rival_driver_id) if a.rival_driver_id is not None else None,
            a.message,
        )
        for a in alert_rows.scalars().all()
    ]

    lap_rows = await db.execute(
        select(LapData).where(
            LapData.session_id == session_id,
            LapData.lap_number >= start_lap - 1,
            LapData.lap_number <= end_lap,
        )
    )
    data.lap_events = [lap_event_payload(lap) for lap in lap_rows.scalars().all()]
    return data


# --- The timing tower: each car's progress round the lap --------------------
#
# The tower used to change only when the leader finished a lap, so a car that
# pitted kept its old place for up to two minutes while the map already showed
# it behind (Day 6 visual check, Belgian GP: VER's lap-17 stop). Ranking at
# every line crossing does not help a pit stop either: the cars that pass a
# car in the pits only reach a line about a lap later.
#
# Instead the tower is recomputed every second from each car's progress in
# laps: between two line crossings a car is assumed to cover the lap at an
# even pace, so its progress is interpolated from the stored crossing times. A
# car on a slow lap (a pit stop, a spin) falls behind as soon as that lap is
# slower than the cars around it, not a lap later. Its known limit: a pit
# stop's lost time is spread evenly over the lap, so a pass can show a few
# seconds before or after the map shows it.

# (seconds from the window's first moment, laps completed at that crossing),
# in time order.
LapLine = list[tuple[float, int]]


def _interpolate(start: tuple[float, float], end: tuple[float, float], x: float) -> float:
    (x0, y0), (x1, y1) = start, end
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


@dataclass(frozen=True)
class CarProgress:
    """One car's line crossings, to tell how far round the lap it is at any moment."""

    crossings: LapLine
    # True when the car finished the window's last lap: after that it keeps
    # going at its last lap's pace. False when it stopped short (retired, or
    # no stored end time): it stays where it last crossed and the field passes.
    keeps_going: bool

    def laps_at(self, t: float) -> float | None:
        """Laps completed at moment t, as a fraction (20.5 = halfway round lap 21).

        Args:
            t: Seconds from the window's first moment.
        Returns:
            The interpolated progress, or None with no crossings at all.
        """
        points = [(time, float(laps)) for time, laps in self.crossings]
        if not points:
            return None
        if len(points) == 1:
            return points[0][1]
        if t <= points[0][0]:
            return _interpolate(points[0], points[1], t)
        for before, after in zip(points, points[1:], strict=False):
            if t <= after[0]:
                return _interpolate(before, after, t)
        return _interpolate(points[-2], points[-1], t) if self.keeps_going else points[-1][1]

    def time_at(self, laps: float) -> float | None:
        """When this car reaches the given progress, at its own pace.

        Extrapolates past its crossings at the nearest lap's pace, so a gap
        can always be given while the car has two crossings.

        Args:
            laps: Progress in laps, as laps_at returns.
        Returns:
            Seconds from the window's first moment, or None with fewer than
            two crossings.
        """
        points = [(float(lap), time) for time, lap in self.crossings]
        if len(points) < 2:
            return None
        if laps <= points[0][0]:
            return _interpolate(points[0], points[1], laps)
        for before, after in zip(points, points[1:], strict=False):
            if laps <= after[0]:
                return _interpolate(before, after, laps)
        return _interpolate(points[-2], points[-1], laps)


@dataclass(frozen=True)
class Tower:
    """What the timing tower is computed from, for one window."""

    session_id: str
    cars: dict[str, CarProgress]
    # Stored snapshot lap -> the cars in it. A car is shown only while it is in
    # the snapshot for the lap the leader is on, so a retired car drops out of
    # the tower once the leader completes a lap it never finished, as before.
    members_by_lap: dict[int, frozenset[str]]
    # Order in the lap-before snapshot: breaks exact ties.
    starting_order: dict[str, int]
    # (driver, lap) -> the tyre that lap was driven on (lap_data.compound).
    compounds: dict[tuple[str, int], str]


def build_tower(data: PlaybackData, origin: float) -> Tower:
    """Each car's line crossings in the window, starting from the lap before.

    The lap before the window ends when the car starts the window's first lap,
    so that start time is its first crossing; every stored lap end after it is
    another.

    Args:
        data: The loaded window.
        origin: window_origin(data).
    Returns:
        The Tower.
    """
    lap_before = data.start_lap - 1
    crossings: dict[str, set[tuple[float, int]]] = {}
    finished_window: set[str] = set()
    for t in data.lap_timings:
        if t.lap_number == data.start_lap and t.lap_start_seconds is not None:
            crossings.setdefault(t.driver_id, set()).add((t.lap_start_seconds - origin, lap_before))
        if t.lap_end_seconds is not None:
            crossings.setdefault(t.driver_id, set()).add((t.lap_end_seconds - origin, t.lap_number))
            if t.lap_number == data.end_lap:
                finished_window.add(t.driver_id)
    cars = {
        driver_id: CarProgress(sorted(points), driver_id in finished_window)
        for driver_id, points in crossings.items()
    }
    members_by_lap = {
        lap_number: frozenset(str(gap["driver_id"]) for gap in payload.get("gaps", []))
        for lap_number, payload in data.gap_snapshots.items()
    }
    starting_order = {
        str(gap["driver_id"]): int(gap["position"])
        for gap in data.gap_snapshots.get(lap_before, {}).get("gaps", [])
    }
    compounds = {
        (str(lap["driver_id"]), int(lap["lap_number"])): str(lap["compound"])
        for lap in data.lap_events
        if lap.get("compound")
    }
    return Tower(str(data.session_id), cars, members_by_lap, starting_order, compounds)


def _current_compound(tower: Tower, driver_id: str, laps: float) -> str | None:
    # The lap being driven is the one after the last completed. Its compound
    # is the new tyre from the line where the pit lap starts, a few seconds
    # before the stop itself. A car with no such lap stored (stopped, or past
    # the window's last lap) keeps the tyre of its latest stored lap.
    driving = math.floor(laps) + 1
    stored = [lap for (d, lap) in tower.compounds if d == driver_id and lap <= driving]
    return tower.compounds[(driver_id, max(stored))] if stored else None


def _members(tower: Tower, leader_laps: int) -> frozenset[str] | None:
    if not tower.members_by_lap:
        return None
    known = [lap for lap in tower.members_by_lap if lap <= leader_laps]
    return tower.members_by_lap[max(known) if known else min(tower.members_by_lap)]


def tower_gaps(tower: Tower, t: float) -> dict[str, Any] | None:
    """The timing tower at moment t, in the shape the gaps key holds.

    Cars are ranked by progress. A car's gap to the one ahead is how long it
    takes, at its own pace, to reach where that car is now; a car a whole lap
    or more behind gets laps_behind instead, as the tower expects.

    Args:
        tower: build_tower's result.
        t: Seconds from the window's first moment.
    Returns:
        A SessionGapsResponse-shaped payload with "source": "replay", or None
        when no car has a crossing yet.
    """
    progress = {d: p for d, car in tower.cars.items() if (p := car.laps_at(t)) is not None}
    if not progress:
        return None
    members = _members(tower, math.floor(max(progress.values())))
    if members is not None:
        progress = {d: p for d, p in progress.items() if d in members}
    order = sorted(
        progress, key=lambda d: (-progress[d], tower.starting_order.get(d, len(tower.cars)), d)
    )

    def interval(ahead: str, behind: str) -> tuple[float | None, int]:
        laps_down = progress[ahead] - progress[behind]
        if laps_down >= 1:
            return None, math.floor(laps_down)
        reaches = tower.cars[behind].time_at(progress[ahead])
        return (None if reaches is None else round(max(0.0, reaches - t), 3)), 0

    gaps = []
    for i, driver_id in enumerate(order):
        gap_ahead, laps_behind = (0.0, 0) if i == 0 else interval(order[i - 1], driver_id)
        gap_behind = 0.0 if i == len(order) - 1 else interval(driver_id, order[i + 1])[0]
        gaps.append(
            {
                "driver_id": driver_id,
                "lap_number": math.floor(progress[driver_id]),
                "position": i + 1,
                "gap_to_ahead_seconds": gap_ahead,
                "gap_to_behind_seconds": gap_behind,
                "laps_behind": laps_behind,
                "compound": _current_compound(tower, driver_id, progress[driver_id]),
            }
        )
    # "source": "replay": live_race_detection treats a gaps key as a live race
    # only when it says "live" (see replay_pipeline._compute_lap_gaps).
    return {"session_id": tower.session_id, "gaps": gaps, "source": "replay"}


# --- The timeline ----------------------------------------------------------

# Order of events that fall on the same moment: lap events first, then the
# alerts those laps raise. (The tower is not an event: it is published every
# tick, before the events that come due on it.)
LAP, ALERT = 1, 2


@dataclass(frozen=True)
class PlaybackEvent:
    """One thing to publish, at `at` seconds after the window's first moment."""

    at: float
    kind: int  # LAP or ALERT
    lap_number: int
    payload: dict[str, Any]
    alert: AlertRow | None = None


# driver_id -> [(seconds from the window's first moment, x, y)], ascending.
PositionTimeline = dict[str, list[tuple[float, float, float]]]


@dataclass
class Timeline:
    """The whole window, ready for the real-time runner."""

    events: list[PlaybackEvent]
    positions: PositionTimeline
    tower: Tower
    duration: float


def window_origin(data: PlaybackData) -> float:
    """The window's first moment: when the first car started the window's first lap.

    That is also when it finished the lap before, so the lap-before lap events
    are current at that instant.

    Args:
        data: The loaded window.
    Returns:
        Session-clock seconds.
    Raises:
        ValueError: no driver has a start time for the window's first lap.
    """
    starts = [
        t.lap_start_seconds
        for t in data.lap_timings
        if t.lap_number == data.start_lap and t.lap_start_seconds is not None
    ]
    if not starts:
        raise ValueError(f"No lap-start time for lap {data.start_lap}; nothing to play back")
    return min(starts)


def build_position_timeline(data: PlaybackData, origin: float) -> PositionTimeline:
    """Place every position sample on one shared clock.

    A sample's time is its lap's start plus its offset into the lap, so two
    drivers far apart on the road stay far apart on the map (replay_pipeline's
    _build_position_timeline, with the stored lap starts instead of FastF1).
    A sample whose lap has no stored start time is dropped.

    Args:
        data: The loaded window.
        origin: window_origin(data).
    Returns:
        Per-driver samples in time order, in seconds from origin.
    """
    lap_start = {
        (t.driver_id, t.lap_number): t.lap_start_seconds
        for t in data.lap_timings
        if t.lap_start_seconds is not None
    }
    timeline: PositionTimeline = {}
    for row in data.positions:
        start = lap_start.get((row.driver_id, row.lap_number))
        if start is None:
            continue
        timeline.setdefault(row.driver_id, []).append(
            (start + row.timestamp_in_lap - origin, row.x, row.y)
        )
    for samples in timeline.values():
        samples.sort(key=lambda sample: sample[0])
    return timeline


def build_events(data: PlaybackData, origin: float) -> list[PlaybackEvent]:
    """Every lap event and alert, in the order to publish them.

    - Lap events: the lap before the window at 0; each lap in the window when
      that driver finished it. A lap with no stored end time is skipped.
    - Alerts: when the trailing driver finished that lap (their prediction for
      the lap is what raised it), or when the first car did if that driver has
      no stored end time.

    Args:
        data: The loaded window.
        origin: window_origin(data).
    Returns:
        Events sorted by time, then LAP before ALERT, then lap and driver, so
        the order is the same on every run.
    """
    lap_end = {
        (t.driver_id, t.lap_number): t.lap_end_seconds - origin
        for t in data.lap_timings
        if t.lap_end_seconds is not None
    }
    first_finish: dict[int, float] = {}
    for (_, lap_number), finished in lap_end.items():
        first_finish[lap_number] = min(finished, first_finish.get(lap_number, finished))

    events: list[PlaybackEvent] = []
    lap_before = data.start_lap - 1
    for payload in data.lap_events:
        lap_number = payload["lap_number"]
        at = 0.0 if lap_number == lap_before else lap_end.get((payload["driver_id"], lap_number))
        if at is not None:
            events.append(PlaybackEvent(at, LAP, lap_number, payload))

    for alert in data.alerts:
        at = lap_end.get((alert.driver_id, alert.lap_number), first_finish.get(alert.lap_number))
        if at is not None:
            payload = {
                "session_id": str(data.session_id),
                "driver_id": alert.driver_id,
                "message": alert.message,
            }
            events.append(PlaybackEvent(at, ALERT, alert.lap_number, payload, alert))

    events.sort(key=lambda e: (e.at, e.kind, e.lap_number, str(e.payload.get("driver_id", ""))))
    return events


def build_timeline(data: PlaybackData) -> Timeline:
    """The window's events and positions on one clock, starting at 0.

    Args:
        data: The loaded window.
    Returns:
        The Timeline; duration covers the last event and the last position.
    Raises:
        ValueError: see window_origin.
    """
    origin = window_origin(data)
    events = build_events(data, origin)
    positions = build_position_timeline(data, origin)
    last_position = max((samples[-1][0] for samples in positions.values()), default=0.0)
    last_event = events[-1].at if events else 0.0
    return Timeline(events, positions, build_tower(data, origin), max(last_position, last_event))


# --- The real-time runner --------------------------------------------------


@dataclass
class PlaybackStats:
    """What one playback published, for the closing log line and the tests."""

    ticks: int = 0
    gaps: int = 0
    laps: int = 0
    alerts: int = 0
    alert_rows: int = 0
    failures: int = 0


# Delivers one ALERT event; returns how many Alert rows it wrote.
AlertSink = Callable[[PlaybackEvent], Awaitable[int]]


def advance_positions(
    positions: PositionTimeline, pointers: dict[str, int], tick: float
) -> dict[str, tuple[float, float]]:
    """Each driver's latest sample at or before tick.

    A driver whose first sample is still ahead is left out, as today's replay
    does: better no dot than a wrong one.

    Args:
        positions: The timeline's positions.
        pointers: driver_id -> index of the sample last shown (-1 before any);
            advanced in place, so each call only scans new samples.
        tick: Seconds from the window's first moment.
    Returns:
        driver_id -> (x, y).
    """
    shown: dict[str, tuple[float, float]] = {}
    for driver_id, samples in positions.items():
        pointer = pointers.get(driver_id, -1)
        while pointer + 1 < len(samples) and samples[pointer + 1][0] <= tick:
            pointer += 1
        pointers[driver_id] = pointer
        if pointer >= 0:
            _, x, y = samples[pointer]
            shown[driver_id] = (x, y)
    return shown


async def _publish_car_numbers(
    client: aioredis.Redis,  # type: ignore[type-arg]
    data: PlaybackData,
) -> None:
    pipeline = client.pipeline(transaction=False)
    for driver_id, car_number in data.car_numbers.items():
        pipeline.setex(
            car_number_key(data.season, data.round_number, driver_id),
            CAR_NUMBER_KEY_TTL_SECONDS,
            car_number,
        )
    await pipeline.execute()


async def _publish_positions(
    client: aioredis.Redis,  # type: ignore[type-arg]
    data: PlaybackData,
    shown: dict[str, tuple[float, float]],
    tick: float,
) -> None:
    # One pipeline per tick: one round trip per driver made a replay's 1 Hz
    # cadence drift (Day 43 verification).
    pipeline = client.pipeline(transaction=False)
    for driver_id, (x, y) in shown.items():
        car_number = data.car_numbers.get(driver_id)
        if car_number is None:
            continue
        payload = {"x": x, "y": y, "z": None, "timestamp": f"replay+{tick:.0f}s"}
        pipeline.setex(
            position_key(data.season, data.round_number, car_number),
            POSITION_KEY_TTL_SECONDS,
            json.dumps(payload),
        )
    await pipeline.execute()


async def _publish_event(
    client: aioredis.Redis,  # type: ignore[type-arg]
    data: PlaybackData,
    event: PlaybackEvent,
    alert_sink: AlertSink,
    stats: PlaybackStats,
) -> None:
    """Publish one event; a failure is logged and counted, never fatal."""
    try:
        if event.kind == LAP:
            await client.publish(f"f1:telemetry:{data.session_id}:laps", json.dumps(event.payload))
            stats.laps += 1
        else:
            stats.alert_rows += await alert_sink(event)
            stats.alerts += 1
    except (RedisError, SQLAlchemyError, ValueError):
        stats.failures += 1
        logger.exception("Could not publish lap %d event (kind %d)", event.lap_number, event.kind)


async def play(
    client: aioredis.Redis,  # type: ignore[type-arg]
    data: PlaybackData,
    timeline: Timeline,
    alert_sink: AlertSink,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> PlaybackStats:
    """Play the timeline in real time, then delete the replay's gaps key.

    One clock, ticking every POSITION_TICK_SECONDS from 0 to the timeline's
    duration. Each tick publishes the timing tower, then the events that have
    come due (so an event is at most a tick late), then every driver's latest
    position. Ticks are
    scheduled from the start time, not from the previous tick, so a slow tick
    does not push the rest of the window later.

    The gaps key is deleted however playback ends (finished, cancelled or
    interrupted) so it can't linger and be read after the replay; position keys
    expire on their own within seconds.

    Args:
        client: Async Redis client.
        data: The loaded window.
        timeline: build_timeline(data).
        alert_sink: Delivers each ALERT event (make_alert_sink in production).
        clock, sleep: Injected in tests.
    Returns:
        What was published.
    """
    stats = PlaybackStats()
    try:
        await _publish_car_numbers(client, data)
        pointers: dict[str, int] = {}
        next_event = 0
        started = clock()
        tick = 0.0
        while True:
            gaps = tower_gaps(timeline.tower, tick)
            if gaps is not None:
                try:
                    await client.setex(
                        gaps_key(data.season, data.round_number),
                        GAPS_KEY_TTL_SECONDS,
                        json.dumps(gaps),
                    )
                    stats.gaps += 1
                except RedisError:
                    stats.failures += 1
                    logger.exception("Could not publish the timing tower at %.0fs", tick)
            while next_event < len(timeline.events) and timeline.events[next_event].at <= tick:
                await _publish_event(client, data, timeline.events[next_event], alert_sink, stats)
                next_event += 1
            shown = advance_positions(timeline.positions, pointers, tick)
            if shown:
                try:
                    await _publish_positions(client, data, shown, tick)
                except RedisError:
                    stats.failures += 1
                    logger.exception("Could not publish positions at %.0fs", tick)
            stats.ticks += 1
            if tick >= timeline.duration:
                break
            tick += POSITION_TICK_SECONDS
            delay = started + tick - clock()
            if delay > 0:
                await sleep(delay)
    finally:
        try:
            await client.delete(gaps_key(data.season, data.round_number))
        except RedisError:
            logger.warning("Could not delete the replay gaps key", exc_info=True)
    return stats


def make_alert_sink(
    session_factory: async_sessionmaker[AsyncSession],
    client: aioredis.Redis,  # type: ignore[type-arg]
) -> AlertSink:
    """An AlertSink writing Alert rows for the alert driver's subscribers.

    Args:
        session_factory: Session factory on this process's engine.
        client: Async Redis client, for the f1:alerts:{session_id} publish.
    Returns:
        The sink.
    """

    async def sink(event: PlaybackEvent) -> int:
        if event.alert is None:
            raise ValueError("ALERT event without an alert row")
        async with session_factory() as db:
            created = await alert_service.dispatch_stored_alert(
                db, client, AlertType(event.alert.alert_type), event.payload
            )
        return len(created)

    return sink


def _redis_client() -> aioredis.Redis:  # type: ignore[type-arg]
    url = get_redis_settings().redis_url
    # Same as core/redis_client.py: verify certificates on a rediss:// URL
    # (Upstash) instead of redis-py's silent unverified fallback.
    tls = {"ssl_cert_reqs": "required"} if url.startswith("rediss://") else {}
    return aioredis.from_url(url, decode_responses=True, **tls)


async def run(session_id: uuid.UUID, start_lap: int, end_lap: int) -> PlaybackStats:
    """Load one curated window and play it back.

    Args:
        session_id: The curated race session.
        start_lap, end_lap: The inclusive curated window.
    Returns:
        What was published.
    """
    engine = get_engine()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    client = _redis_client()
    try:
        async with session_factory() as db:
            data = await load_playback_data(db, session_id, start_lap, end_lap)
        timeline = build_timeline(data)
        logger.info(
            "Playing back laps %d-%d: %d events, %d cars on the map, %.0fs",
            start_lap,
            end_lap,
            len(timeline.events),
            len(timeline.positions),
            timeline.duration,
        )
        return await play(client, data, timeline, make_alert_sink(session_factory, client))
    finally:
        await client.aclose()  # type: ignore[attr-defined]
        await engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Play back a precomputed Demo Replay window in real time, without a worker."
    )
    parser.add_argument("--session-id", type=uuid.UUID, required=True)
    parser.add_argument("--start-lap", type=int, required=True)
    parser.add_argument("--end-lap", type=int, required=True)
    args = parser.parse_args()
    if args.end_lap < args.start_lap:
        parser.error("--end-lap must be >= --start-lap")
    return args


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args()
    guard_against_live_race()
    signal.signal(signal.SIGTERM, reraise_sigterm_as_interrupt)
    try:
        stats = asyncio.run(run(args.session_id, args.start_lap, args.end_lap))
    except KeyboardInterrupt:
        # SIGTERM (/demo/replay/stop, the live-race kill-switch) or Ctrl+C.
        # asyncio.run cancels play() on the way out, so its finally has
        # already deleted the gaps key.
        logger.info("Playback stopped")
        return
    except ValueError:
        logger.exception("Nothing to play back")
        raise SystemExit(1) from None
    logger.info(
        "Playback finished: %d tower updates, %d lap events, %d alerts (%d rows), %d failures",
        stats.gaps,
        stats.laps,
        stats.alerts,
        stats.alert_rows,
        stats.failures,
    )


if __name__ == "__main__":
    main()
