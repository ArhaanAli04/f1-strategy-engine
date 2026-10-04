"""Unit tests for scripts/replay_playback.py: the timeline (pure) and the
real-time runner (fake Redis, fake clock; no DB).

A hand-built window: cars A, B, C over laps 20-21 (lap 19 is the lap before),
on a session clock where A leads and starts lap 20 at 1000 s.
"""

import asyncio
import json
import subprocess
import sys
import uuid
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

import fakeredis as fakeredis_lib
import pytest
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from backend.scripts import replay_playback as pb

SESSION = uuid.uuid4()
A, B, C = "a", "b", "c"

# (driver, lap) -> (start, end) on the session clock. B is 2 s behind A, C 30 s.
TIMES = {
    (A, 20): (1000.0, 1090.0),
    (B, 20): (1002.0, 1092.0),
    (C, 20): (1030.0, 1121.0),
    (A, 21): (1090.0, 1181.0),
    (B, 21): (1092.0, 1180.0),  # B passes A on the line
    (C, 21): (1121.0, 1212.0),
}


def _lap_event(driver: str, lap: int) -> dict[str, Any]:
    return {"driver_id": driver, "session_id": str(SESSION), "lap_number": lap}


def _snapshot(*order: str) -> dict[str, Any]:
    """A stored gap snapshot; the tower reads only who is in it and their order."""
    return {"gaps": [{"driver_id": d, "position": i + 1} for i, d in enumerate(order)]}


def _timings(
    times: Mapping[tuple[str, int], tuple[float | None, float | None]],
) -> list[pb.LapTimingRow]:
    return [pb.LapTimingRow(d, lap, s, e) for (d, lap), (s, e) in times.items()]


def _data(**overrides: Any) -> pb.PlaybackData:
    data = pb.PlaybackData(SESSION, 2026, 9, 20, 21)
    data.lap_timings = _timings(TIMES)
    data.gap_snapshots = {lap: _snapshot(A, B, C) for lap in (19, 20, 21)}
    data.lap_events = [_lap_event(d, lap) for d in (A, B, C) for lap in (19, 20, 21)]
    data.alerts = [pb.AlertRow(21, "UNDERCUT_THREAT", C, B, "Undercut threat: C on B (60.0%)")]
    for key, value in overrides.items():
        setattr(data, key, value)
    return data


def _at(events: list[pb.PlaybackEvent], kind: int) -> list[tuple[float, int, Any]]:
    return [(e.at, e.lap_number, e.payload.get("driver_id")) for e in events if e.kind == kind]


# --- origin ---


@pytest.mark.unit
def test_origin_is_the_first_start_of_the_windows_first_lap() -> None:
    assert pb.window_origin(_data()) == 1000.0


@pytest.mark.unit
def test_origin_without_any_first_lap_start_is_refused() -> None:
    timings = [pb.LapTimingRow(A, 20, None, 1090.0)]
    with pytest.raises(ValueError, match="lap 20"):
        pb.window_origin(_data(lap_timings=timings))


# --- the timing tower (progress round the lap) ---


def _tower_at(data: pb.PlaybackData, t: float) -> list[tuple[str, Any, int]]:
    """(driver, gap to the car ahead, laps behind) in tower order at t."""
    payload = pb.tower_gaps(pb.build_tower(data, 1000.0), t)
    assert payload is not None
    assert payload["source"] == "replay"
    return [(g["driver_id"], g["gap_to_ahead_seconds"], g["laps_behind"]) for g in payload["gaps"]]


@pytest.mark.unit
def test_progress_is_interpolated_between_line_crossings() -> None:
    car = pb.CarProgress([(0.0, 19), (90.0, 20), (181.0, 21)], keeps_going=True)

    assert car.laps_at(45.0) == pytest.approx(19.5)
    assert car.laps_at(90.0) == pytest.approx(20.0)
    assert car.laps_at(272.0) == pytest.approx(22.0)  # past the window, at its last pace
    assert car.time_at(20.5) == pytest.approx(135.5)


@pytest.mark.unit
def test_a_car_that_stopped_stays_where_it_last_crossed() -> None:
    car = pb.CarProgress([(0.0, 19), (90.0, 20)], keeps_going=False)
    assert car.laps_at(500.0) == pytest.approx(20.0)


@pytest.mark.unit
def test_tower_at_the_first_moment_and_gaps_are_time_to_reach_the_car_ahead() -> None:
    """B is 2 s from the line, so 2.0 behind A. C needs 27.978 s, not 28, to
    reach where B is now: it covers B's last 2 s of lap at its own, slower pace
    (91 s laps against B's 90 s)."""
    assert _tower_at(_data(), 0.0) == [(A, 0.0, 0), (B, 2.0, 0), (C, 27.978, 0)]


@pytest.mark.unit
def test_tower_follows_a_pass_at_the_line() -> None:
    """B finishes lap 21 at 1180, before A at 1181."""
    assert [d for d, _, _ in _tower_at(_data(), 180.5)] == [B, A, C]


@pytest.mark.unit
def test_a_slow_lap_drops_a_car_back_before_either_car_reaches_the_line() -> None:
    """B pits on lap 20 (a 120 s lap): C, 28 s behind on a 91 s lap, passes it
    at about 117.9 s, before B (122 s) or C (121 s) finishes lap 20."""
    times = dict(TIMES)
    times[(B, 20)] = (1002.0, 1122.0)
    times[(B, 21)] = (1122.0, 1210.0)
    data = _data(lap_timings=_timings(times))

    assert [d for d, _, _ in _tower_at(data, 117.0)] == [A, B, C]
    assert [d for d, _, _ in _tower_at(data, 119.0)] == [A, C, B]


@pytest.mark.unit
def test_a_retired_car_leaves_the_tower_once_the_leader_completes_a_lap_it_never_did() -> None:
    times: dict[tuple[str, int], tuple[float | None, float | None]] = {
        k: v for k, v in TIMES.items() if k[0] != C
    }
    times[(C, 20)] = (1030.0, None)  # stopped on track during lap 20
    snapshots = {19: _snapshot(A, B, C), 20: _snapshot(A, B), 21: _snapshot(B, A)}
    data = _data(lap_timings=_timings(times), gap_snapshots=snapshots)

    assert [d for d, _, _ in _tower_at(data, 50.0)] == [A, B, C]
    assert [d for d, _, _ in _tower_at(data, 95.0)] == [A, B]


@pytest.mark.unit
def test_a_car_a_lap_or_more_down_gets_laps_behind_instead_of_a_gap() -> None:
    times = dict(TIMES)
    times[(C, 20)] = (1030.0, 1300.0)  # a 270 s lap
    times[(C, 21)] = (1300.0, 1391.0)
    data = _data(lap_timings=_timings(times))

    *_, (driver, gap, laps_behind) = _tower_at(data, 200.0)
    assert (driver, gap, laps_behind) == (C, None, 1)


@pytest.mark.unit
def test_the_tower_shows_the_tyre_of_the_lap_each_car_is_on() -> None:
    """B pits at the end of lap 20 (crosses at 92 s) and drives lap 21 on HARD:
    the tower shows HARD from that line, not when lap 21 is completed (180 s)."""
    laps = [
        {**_lap_event(d, lap), "compound": "HARD" if (d, lap) == (B, 21) else "MEDIUM"}
        for d in (A, B, C)
        for lap in (19, 20, 21)
    ]
    tower = pb.build_tower(_data(lap_events=laps), 1000.0)

    def tyres(t: float) -> dict[str, Any]:
        payload = pb.tower_gaps(tower, t)
        assert payload is not None
        return {g["driver_id"]: g["compound"] for g in payload["gaps"]}

    assert tyres(50.0) == {A: "MEDIUM", B: "MEDIUM", C: "MEDIUM"}
    assert tyres(95.0) == {A: "MEDIUM", B: "HARD", C: "MEDIUM"}
    assert tyres(300.0)[B] == "HARD"  # past the window: the last lap's tyre


@pytest.mark.unit
def test_no_tyre_is_shown_when_no_lap_carries_one() -> None:
    payload = pb.tower_gaps(pb.build_tower(_data(), 1000.0), 50.0)
    assert payload is not None
    assert {g["compound"] for g in payload["gaps"]} == {None}


@pytest.mark.unit
def test_no_tower_before_any_car_has_crossed() -> None:
    assert pb.tower_gaps(pb.build_tower(_data(lap_timings=[]), 1000.0), 0.0) is None


# --- lap events ---


@pytest.mark.unit
def test_lap_before_events_at_zero_and_window_laps_when_each_driver_finished() -> None:
    events = pb.build_events(_data(), 1000.0)

    assert _at(events, pb.LAP) == [
        (0.0, 19, A),
        (0.0, 19, B),
        (0.0, 19, C),
        (90.0, 20, A),
        (92.0, 20, B),
        (121.0, 20, C),
        (180.0, 21, B),
        (181.0, 21, A),
        (212.0, 21, C),
    ]


@pytest.mark.unit
def test_a_lap_with_no_stored_end_time_sends_no_event() -> None:
    timings = [t for t in _data().lap_timings if (t.driver_id, t.lap_number) != (C, 21)]
    timings.append(pb.LapTimingRow(C, 21, 1121.0, None))  # stopped on track

    events = pb.build_events(_data(lap_timings=timings), 1000.0)

    assert (21, C) not in [
        (e.lap_number, e.payload["driver_id"]) for e in events if e.kind == pb.LAP
    ]


# --- alerts ---


@pytest.mark.unit
def test_an_alert_fires_when_the_trailing_driver_finishes_that_lap() -> None:
    events = pb.build_events(_data(), 1000.0)

    (alert,) = [e for e in events if e.kind == pb.ALERT]
    assert alert.at == 212.0  # C's lap 21 end
    assert alert.payload == {
        "session_id": str(SESSION),
        "driver_id": C,
        "message": "Undercut threat: C on B (60.0%)",
    }
    assert alert.alert is not None
    assert alert.alert.rival_driver_id == B


@pytest.mark.unit
def test_an_alert_whose_driver_has_no_end_time_fires_when_the_first_car_finishes() -> None:
    timings = [t for t in _data().lap_timings if (t.driver_id, t.lap_number) != (C, 21)]

    events = pb.build_events(_data(lap_timings=timings), 1000.0)

    assert [e.at for e in events if e.kind == pb.ALERT] == [180.0]


# --- ordering ---


@pytest.mark.unit
def test_at_the_first_moment_only_the_lap_before_lap_events_are_due() -> None:
    events = pb.build_events(_data(), 1000.0)
    at_zero = [e.kind for e in events if e.at == 0.0]
    assert at_zero == [pb.LAP, pb.LAP, pb.LAP]


@pytest.mark.unit
def test_an_alert_on_the_same_moment_as_its_lap_event_comes_after_it() -> None:
    events = pb.build_events(_data(), 1000.0)
    at_212 = [e.kind for e in events if e.at == 212.0]
    assert at_212 == [pb.LAP, pb.ALERT]


@pytest.mark.unit
def test_events_are_in_time_order() -> None:
    times = [e.at for e in pb.build_events(_data(), 1000.0)]
    assert times == sorted(times)


# --- positions ---


@pytest.mark.unit
def test_positions_are_placed_on_one_clock_so_real_gaps_survive() -> None:
    """A and C are both 5 s into lap 20, but C started it 30 s later."""
    positions = [
        pb.PositionRow(A, 20, 5.0, 1.0, 1.0),
        pb.PositionRow(C, 20, 5.0, 9.0, 9.0),
        pb.PositionRow(A, 20, 1.0, 0.0, 0.0),
    ]

    timeline = pb.build_position_timeline(_data(positions=positions), 1000.0)

    assert timeline[A] == [(1.0, 0.0, 0.0), (5.0, 1.0, 1.0)]
    assert timeline[C] == [(35.0, 9.0, 9.0)]


@pytest.mark.unit
def test_a_position_whose_lap_has_no_start_time_is_dropped() -> None:
    timings = [t for t in _data().lap_timings if (t.driver_id, t.lap_number) != (B, 21)]
    timings.append(pb.LapTimingRow(B, 21, None, 1180.0))
    positions = [pb.PositionRow(B, 21, 3.0, 2.0, 2.0)]

    timeline = pb.build_position_timeline(_data(lap_timings=timings, positions=positions), 1000.0)

    assert timeline == {}


# --- whole timeline ---


@pytest.mark.unit
def test_timeline_runs_until_the_later_of_the_last_event_and_the_last_position() -> None:
    positions = [pb.PositionRow(C, 21, 95.0, 0.0, 0.0)]  # 1121 + 95 = 1216 > last event 1212

    timeline = pb.build_timeline(_data(positions=positions))

    assert timeline.duration == 216.0
    assert timeline.events[0].at == 0.0


@pytest.mark.unit
def test_lap_event_payload_is_the_eight_fields_process_lap_publishes() -> None:
    driver_id = uuid.uuid4()
    lap = SimpleNamespace(
        driver_id=driver_id,
        session_id=SESSION,
        lap_number=20,
        lap_time_seconds=90.1,
        compound="HARD",
        sector1_seconds=30.0,
        sector2_seconds=31.0,
        sector3_seconds=29.1,
        tyre_age_laps=12,  # not part of the event
    )

    assert pb.lap_event_payload(lap) == {  # type: ignore[arg-type]
        "driver_id": str(driver_id),
        "session_id": str(SESSION),
        "lap_number": 20,
        "lap_time_seconds": 90.1,
        "compound": "HARD",
        "sector1_seconds": 30.0,
        "sector2_seconds": 31.0,
        "sector3_seconds": 29.1,
    }


# --- the real-time runner (fake Redis, fake clock) ---


class _Clock:
    """Time only moves when playback sleeps, so a test runs instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps = 0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds


class _Recorder:
    """Wraps a fake Redis client, noting when each publish and setex happens."""

    def __init__(self, client: Any, clock: _Clock) -> None:
        self.client = client
        self.clock = clock
        self.published: list[tuple[float, str, dict[str, Any]]] = []
        self.gaps_set: list[tuple[float, dict[str, Any]]] = []
        self.failing_publishes = 0

    async def publish(self, channel: str, message: str) -> int:
        if self.failing_publishes:
            self.failing_publishes -= 1
            raise RedisError("down")
        self.published.append((self.clock.now, channel, json.loads(message)))
        return 0

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.gaps_set.append((self.clock.now, json.loads(value)))
        await self.client.setex(key, ttl, value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)


def _runner_data() -> pb.PlaybackData:
    data = _data()
    data.car_numbers = {A: "1", B: "44", C: "16"}
    data.positions = [
        pb.PositionRow(A, 20, 0.0, 0.0, 0.0),
        pb.PositionRow(A, 20, 50.0, 5.0, 5.0),
        pb.PositionRow(C, 20, 0.0, 9.0, 9.0),  # starts lap 20 at 30 s
    ]
    return data


async def _no_alerts_or_count(event: pb.PlaybackEvent) -> int:
    return 0


@pytest.fixture
def fake() -> Any:
    return fakeredis_lib.FakeAsyncRedis(decode_responses=True)


@pytest.mark.unit
async def test_play_publishes_every_event_when_it_comes_due(fake: Any) -> None:
    clock = _Clock()
    client: Any = _Recorder(fake, clock)  # stands in for the Redis client
    delivered: list[tuple[float, str]] = []

    async def sink(event: pb.PlaybackEvent) -> int:
        delivered.append((clock.now, event.payload["driver_id"]))
        return 3

    data = _runner_data()
    stats = await pb.play(client, data, pb.build_timeline(data), sink, clock, clock.sleep)

    laps = [(at, p["driver_id"], p["lap_number"]) for at, ch, p in client.published]
    assert laps[:3] == [(0.0, A, 19), (0.0, B, 19), (0.0, C, 19)]
    assert (90.0, A, 20) in laps
    assert (212.0, C, 21) in laps
    assert all(ch == f"f1:telemetry:{SESSION}:laps" for _, ch, _ in client.published)
    # The tower every tick, 0 s to 212 s; B passes A on the line at 180 s.
    assert [at for at, _ in client.gaps_set] == [float(s) for s in range(213)]
    order = {at: [g["driver_id"] for g in gaps["gaps"]] for at, gaps in client.gaps_set}
    assert order[0.0] == [A, B, C]
    assert order[181.0] == [B, A, C]
    assert delivered == [(212.0, C)]
    assert stats == pb.PlaybackStats(
        ticks=213, gaps=213, laps=9, alerts=1, alert_rows=3, failures=0
    )


@pytest.mark.unit
async def test_play_sets_car_numbers_and_the_latest_positions(fake: Any) -> None:
    clock = _Clock()
    data = _runner_data()

    await pb.play(fake, data, pb.build_timeline(data), _no_alerts_or_count, clock, clock.sleep)

    assert await fake.get("f1:2026:9:driver:a:car_number") == "1"
    assert await fake.get("f1:2026:9:driver:c:car_number") == "16"
    last_a = json.loads(await fake.get("f1:2026:9:car:1:position"))
    assert (last_a["x"], last_a["y"], last_a["z"]) == (5.0, 5.0, None)
    assert json.loads(await fake.get("f1:2026:9:car:16:position"))["x"] == 9.0
    assert await fake.get("f1:2026:9:car:44:position") is None  # B has no samples


@pytest.mark.unit
async def test_play_deletes_the_gaps_key_when_it_finishes(fake: Any) -> None:
    clock = _Clock()
    data = _runner_data()

    await pb.play(fake, data, pb.build_timeline(data), _no_alerts_or_count, clock, clock.sleep)

    assert await fake.exists("f1:2026:9:gaps") == 0


@pytest.mark.unit
async def test_play_deletes_the_gaps_key_when_stopped_part_way(fake: Any) -> None:
    """SIGTERM ends asyncio.run by cancelling play(); its finally must still clean up."""
    clock = _Clock()

    async def sleep_then_stop(seconds: float) -> None:
        await clock.sleep(seconds)
        if clock.now >= 100:
            raise asyncio.CancelledError

    data = _runner_data()
    with pytest.raises(asyncio.CancelledError):
        await pb.play(
            fake, data, pb.build_timeline(data), _no_alerts_or_count, clock, sleep_then_stop
        )

    assert await fake.exists("f1:2026:9:gaps") == 0


@pytest.mark.unit
async def test_ticks_follow_the_start_time_so_a_slow_tick_does_not_delay_the_rest(
    fake: Any,
) -> None:
    clock = _Clock()
    slept: list[float] = []

    async def slow_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock.now += seconds + (0.4 if len(slept) == 1 else 0.0)  # the first tick overruns

    data = _runner_data()
    await pb.play(fake, data, pb.build_timeline(data), _no_alerts_or_count, clock, slow_sleep)

    assert slept[1] == pytest.approx(0.6)  # caught up on the next tick
    assert clock.now == pytest.approx(212.0)


@pytest.mark.unit
async def test_a_failed_publish_is_counted_and_playback_carries_on(fake: Any) -> None:
    clock = _Clock()
    client: Any = _Recorder(fake, clock)  # stands in for the Redis client
    client.failing_publishes = 1  # the very first lap event

    async def failing_sink(event: pb.PlaybackEvent) -> int:
        raise SQLAlchemyError("db down")

    data = _runner_data()
    stats = await pb.play(client, data, pb.build_timeline(data), failing_sink, clock, clock.sleep)

    assert stats.failures == 2  # one lap event, one alert
    assert stats.laps == 8
    assert stats.alerts == 0


@pytest.mark.unit
def test_advance_positions_shows_each_drivers_latest_sample_and_skips_those_not_started() -> None:
    positions: pb.PositionTimeline = {A: [(0.0, 0.0, 0.0), (2.0, 2.0, 2.0)], C: [(5.0, 9.0, 9.0)]}
    pointers: dict[str, int] = {}

    assert pb.advance_positions(positions, pointers, 1.0) == {A: (0.0, 0.0)}
    assert pb.advance_positions(positions, pointers, 5.0) == {A: (2.0, 2.0), C: (9.0, 9.0)}
    assert pointers == {A: 1, C: 0}


@pytest.mark.unit
def test_importing_playback_loads_no_heavy_libraries() -> None:
    """It runs on the web machine: no FastF1, pandas, numpy, Celery or ML libraries."""
    probe = (
        "import sys, backend.scripts.replay_playback; "
        "heavy = ('fastf1', 'pandas', 'numpy', 'xgboost', 'lightgbm', 'shap', "
        "'sklearn', 'celery'); "
        "print(','.join(m for m in heavy if m in sys.modules))"
    )
    result = subprocess.run(  # noqa: S603 — fixed argv
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""
