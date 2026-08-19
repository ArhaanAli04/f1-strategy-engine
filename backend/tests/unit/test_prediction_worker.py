"""Unit tests for workers/prediction_worker.py.

mock_db_session (AsyncMock spec'd to AsyncSession) stands in for the DB; the real
fakeredis fixture stands in for Redis. _build_race_state's own cumulative-time lookup
is a single batched GROUP BY query (not a per-driver call to _cumulative_race_time —
see Day 35's N+1 fix), so it's mocked the same way as the other db.execute() calls in
this file rather than monkeypatched; _cumulative_race_time itself (still used by
_resolve_position_context, unrelated to _build_race_state) is covered indirectly by
strategy_service's equivalent tests.
"""

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis as fakeredis_lib
import pytest

from backend.workers import prediction_worker


@pytest.mark.unit
async def test_build_race_state_batches_cumulative_time_into_one_query(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """cumulative_race_time_seconds for every driver must come from a single batched
    GROUP BY query anchored to current_lap, not one db.execute() call per driver (the
    N+1 this function previously had: ~20 separate round trips for a 20-driver field).
    Also covers the original current_lap-anchoring invariant this test predates: driver
    A's latest DB row is ahead of current_lap (56 > 55), driver B's is behind it
    (54 < 55) — the batched query's lap_number <= current_lap filter must apply
    uniformly to both regardless of either driver's own latest lap_number, otherwise
    normal async ingestion skew bakes a fake multi-lap time gap into the simulation's
    starting point.
    """
    session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    requesting_driver_id = uuid.uuid4()
    driver_a_id = uuid.uuid4()
    driver_b_id = uuid.uuid4()
    current_lap = 55
    season, round_number = 2025, 22

    context_result = MagicMock()
    context_result.one.return_value = (circuit_id, season, round_number, "Yas Marina Circuit")

    lap_a = SimpleNamespace(
        driver_id=driver_a_id, lap_number=56, compound="MEDIUM", tyre_age_laps=10, position=1
    )
    lap_b = SimpleNamespace(
        driver_id=driver_b_id, lap_number=54, compound="HARD", tyre_age_laps=20, position=2
    )
    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = [lap_a, lap_b]

    position_result = MagicMock()
    position_result.all.return_value = [
        (driver_a_id, lap_a.position),
        (driver_b_id, lap_b.position),
    ]

    cumulative_time_result = MagicMock()
    cumulative_time_result.all.return_value = [
        (driver_a_id, 4321.5),
        (driver_b_id, 4310.0),
    ]

    captured_queries: list[Any] = []

    async def _execute_side_effect(query: Any, *args: Any, **kwargs: Any) -> Any:
        captured_queries.append(query)
        if len(captured_queries) == 1:
            return context_result
        if len(captured_queries) == 2:
            return latest_laps_result
        if len(captured_queries) == 3:
            return position_result
        if len(captured_queries) == 4:
            return cumulative_time_result
        raise AssertionError(f"unexpected extra db.execute call: {query}")

    mock_db_session.execute.side_effect = _execute_side_effect

    # Weather cache hit so _resolve_weather never falls through to an extra db.execute().
    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 40.0, "air_temp": 28.0}),
    )

    race_state = await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        requesting_driver_id,
        current_lap,
        "SOFT",
        3,
        58,
    )

    # Exactly one query for cumulative time regardless of field size — the N+1 fix
    # this test guards: 4 total db.execute() calls (context, latest_laps, position,
    # cumulative_time), never one more per driver.
    assert len(captured_queries) == 4

    cumulative_time_query = captured_queries[3]
    compiled = str(cumulative_time_query.compile(compile_kwargs={"literal_binds": True}))
    # Targeted on the "lap_number <= N" clause specifically, not a bare substring
    # check on the numbers themselves — a bare `str(current_lap) in compiled` (or
    # the drivers' own lap numbers) is a false-negative trap: session_id/driver_id
    # UUIDs are also in this compiled SQL (literal_binds renders them inline) and
    # can coincidentally contain the same digits as a substring.
    assert f"lap_number <= {current_lap}" in compiled
    assert f"lap_number <= {lap_a.lap_number}" not in compiled
    assert f"lap_number <= {lap_b.lap_number}" not in compiled

    driver_a_state = next(d for d in race_state.drivers if d.driver_id == str(driver_a_id))
    driver_b_state = next(d for d in race_state.drivers if d.driver_id == str(driver_b_id))
    assert driver_a_state.cumulative_race_time_seconds == 4321.5
    assert driver_b_state.cumulative_race_time_seconds == 4310.0


@pytest.mark.unit
async def test_build_race_state_starting_position_uses_current_lap_not_final_position(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """starting_position must reflect the driver's position AT current_lap, not
    their absolute-latest DB row's position — for a completed session (or any
    session where ingestion has continued past current_lap), that latest row is
    the FINAL classification position, not the position at the point the what-if
    actually starts (the bug this test guards against: the driver's lap 58 row
    shows position=9, but their real position at lap 55 was 10 — starting_position
    must come out as 10, not 9).
    """
    session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    current_lap = 55
    season, round_number = 2025, 22

    context_result = MagicMock()
    context_result.one.return_value = (circuit_id, season, round_number, "Yas Marina Circuit")

    # This driver's absolute-latest DB row is the race's final lap (58), where
    # they classified P9 — but at lap 55 (current_lap) they were P10.
    final_lap = SimpleNamespace(
        driver_id=driver_id, lap_number=58, compound="MEDIUM", tyre_age_laps=17, position=9
    )
    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = [final_lap]

    position_result = MagicMock()
    position_result.all.return_value = [(driver_id, 10)]

    cumulative_time_result = MagicMock()
    cumulative_time_result.all.return_value = [(driver_id, 0.0)]

    mock_db_session.execute.side_effect = [
        context_result,
        latest_laps_result,
        position_result,
        cumulative_time_result,
    ]

    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 40.0, "air_temp": 28.0}),
    )

    race_state = await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        driver_id,
        current_lap,
        "MEDIUM",
        14,
        58,
    )

    driver_state = next(d for d in race_state.drivers if d.driver_id == str(driver_id))
    assert driver_state.starting_position == 10
    assert driver_state.starting_position != final_lap.position


@pytest.mark.unit
async def test_build_race_state_position_query_filters_by_session_id(
    mock_db_session: AsyncMock,
    fakeredis: fakeredis_lib.FakeAsyncRedis,
) -> None:
    """The position-as-of-current_lap query must filter by session_id on the
    outer select, not just inside its subquery — otherwise (the regression this
    test guards against) the join matches ANY session's LapData row sharing
    (driver_id, lap_number), which is a real collision: this codebase's
    multi-season training corpus means the same driver commonly has a row at
    the same lap_number in unrelated sessions, each with a different position.

    Simulates driver X with a lap_number=55 row in the target session
    (position=10, the value that must be used) and a different session
    (other_session_id, position=99, what an unfiltered join could accidentally
    pick up instead) — asserts the constructed query's WHERE clause actually
    filters on session_id (proving the SQL-level fix, not just the mocked
    result), and that _build_race_state threads the correct value through.
    """
    session_id = uuid.uuid4()
    other_session_id = uuid.uuid4()
    circuit_id = uuid.uuid4()
    driver_id = uuid.uuid4()
    current_lap = 55
    season, round_number = 2025, 22

    context_result = MagicMock()
    context_result.one.return_value = (circuit_id, season, round_number, "Yas Marina Circuit")

    latest_lap = SimpleNamespace(
        driver_id=driver_id, lap_number=55, compound="MEDIUM", tyre_age_laps=14, position=10
    )
    latest_laps_result = MagicMock()
    latest_laps_result.scalars.return_value.all.return_value = [latest_lap]

    # What a correctly session_id-filtered query returns from a real DB — the
    # other_session_id=99 row is excluded by Postgres, not filtered by this mock.
    position_result = MagicMock()
    position_result.all.return_value = [(driver_id, 10)]

    cumulative_time_result = MagicMock()
    cumulative_time_result.all.return_value = [(driver_id, 0.0)]

    captured_queries: list[Any] = []

    async def _execute_side_effect(query: Any, *args: Any, **kwargs: Any) -> Any:
        captured_queries.append(query)
        if len(captured_queries) == 1:
            return context_result
        if len(captured_queries) == 2:
            return latest_laps_result
        if len(captured_queries) == 3:
            return position_result
        if len(captured_queries) == 4:
            return cumulative_time_result
        raise AssertionError(f"unexpected extra db.execute call: {query}")

    mock_db_session.execute.side_effect = _execute_side_effect

    await fakeredis.set(
        prediction_worker._weather_key(season, round_number),
        json.dumps({"track_temp": 40.0, "air_temp": 28.0}),
    )

    race_state = await prediction_worker._build_race_state(
        mock_db_session,
        fakeredis,
        session_id,
        driver_id,
        current_lap,
        "MEDIUM",
        14,
        58,
    )

    position_query = captured_queries[2]
    assert position_query.whereclause is not None, (
        "position_query must filter by session_id on the outer select — without "
        "it, the join matches this driver's lap_number row in ANY session, not "
        "just this one"
    )
    compiled = str(position_query.whereclause.compile(compile_kwargs={"literal_binds": True}))
    assert session_id.hex in compiled.replace("-", "")
    assert other_session_id.hex not in compiled.replace("-", "")

    driver_state = next(d for d in race_state.drivers if d.driver_id == str(driver_id))
    assert driver_state.starting_position == 10
